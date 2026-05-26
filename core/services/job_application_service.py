"""
Business logic for the internal-mobility ``Application`` workflow.

Responsibilities:
* enforce allowed status transitions (a state machine, not free-form writes);
* capture decision metadata (``decided_by`` / ``decided_at`` / ``decision_note``)
  when an application reaches a terminal state;
* fire an in-app notification to the applicant on every terminal transition;
* expose an applicant-side ``withdraw`` entry point.

Views and serializers must call these helpers instead of mutating
``Application`` directly.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from core.enums import ApplicationStatus
from core.models import Application, Notification, UserProfile
from core.services.notification_service import create_notification

# ── State machine ────────────────────────────────────────────────────────────

#: Allowed forward transitions per current status. Terminal states map to an
#: empty set (no further changes accepted from the API).
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.UNDER_REVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.UNDER_REVIEW: {
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.SHORTLISTED: {
        ApplicationStatus.ACCEPTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.ACCEPTED: set(),
    ApplicationStatus.REJECTED: set(),
    ApplicationStatus.WITHDRAWN: set(),
}

TERMINAL_STATUSES: set[str] = {
    ApplicationStatus.ACCEPTED,
    ApplicationStatus.REJECTED,
    ApplicationStatus.WITHDRAWN,
}

#: Statuses an HR/reviewer may set via the PATCH endpoint. ``withdrawn`` is
#: reserved for the applicant's own ``withdraw`` action.
REVIEWER_SETTABLE_STATUSES: set[str] = {
    ApplicationStatus.UNDER_REVIEW,
    ApplicationStatus.SHORTLISTED,
    ApplicationStatus.ACCEPTED,
    ApplicationStatus.REJECTED,
}


def allowed_next_statuses(current_status: str) -> set[str]:
    """Return the set of statuses an application in ``current_status`` may move to."""
    return ALLOWED_TRANSITIONS.get(current_status, set())


def validate_transition(current_status: str, next_status: str) -> None:
    """Raise ``ValidationError`` if ``next_status`` is not a legal move."""
    if current_status == next_status:
        # Idempotent no-op is rejected to surface UI bugs early.
        raise ValidationError(
            {"status": f"Application is already in status '{current_status}'."}
        )
    if next_status not in allowed_next_statuses(current_status):
        raise ValidationError(
            {
                "status": (
                    f"Illegal transition '{current_status}' → '{next_status}'. "
                    f"Allowed: {sorted(allowed_next_statuses(current_status))}."
                )
            }
        )


# ── Service entry points ─────────────────────────────────────────────────────


def _is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def _stamp_decision(
    application: Application,
    *,
    actor: UserProfile | None,
    note: str,
) -> None:
    """Set decision_note/decided_by/decided_at on terminal transitions only."""
    application.decision_note = (note or "").strip()
    application.decided_by = actor
    application.decided_at = timezone.now()


def _notify_applicant(application: Application, *, new_status: str) -> None:
    """Drop an in-app Notification on the applicant's bell for terminal moves.

    Notification failures are swallowed by ``create_notification``; never
    raise from here.
    """
    titles = {
        ApplicationStatus.ACCEPTED: "Your application was accepted",
        ApplicationStatus.REJECTED: "Update on your application",
        ApplicationStatus.WITHDRAWN: "Application withdrawn",
        ApplicationStatus.UNDER_REVIEW: "Your application is under review",
        ApplicationStatus.SHORTLISTED: "You've been shortlisted",
    }
    title = titles.get(new_status, f"Application status: {new_status}")
    listing_title = application.listing.title if application.listing_id else ""
    create_notification(
        recipient=application.applicant,
        title=title,
        message=(
            f'Status for the role "{listing_title}" is now '
            f"'{application.get_status_display()}'."
        ),
        module=Notification.Module.GENERAL,
        type=(
            Notification.Type.SUCCESS
            if new_status == ApplicationStatus.ACCEPTED
            else Notification.Type.INFO
        ),
        metadata={
            "application_id": application.pk,
            "listing_id": application.listing_id,
            "status": new_status,
        },
    )


@transaction.atomic
def transition_application(
    application: Application,
    *,
    new_status: str,
    actor: UserProfile | None,
    note: str = "",
) -> Application:
    """Apply a reviewer-driven status transition.

    Use for HR / listing-owner / department-manager actions. The applicant's
    own ``withdraw`` flow goes through :func:`withdraw_application`.
    """
    if new_status not in REVIEWER_SETTABLE_STATUSES:
        raise ValidationError(
            {
                "status": (
                    f"Reviewers may only set one of "
                    f"{sorted(REVIEWER_SETTABLE_STATUSES)}."
                )
            }
        )

    validate_transition(application.status, new_status)

    application.status = new_status
    if _is_terminal(new_status):
        _stamp_decision(application, actor=actor, note=note)
    application.save(
        update_fields=[
            "status",
            "decision_note",
            "decided_by",
            "decided_at",
            "updated_at",
        ]
    )
    _notify_applicant(application, new_status=new_status)
    return application


@transaction.atomic
def withdraw_application(
    application: Application,
    *,
    actor: UserProfile,
    note: str = "",
) -> Application:
    """Applicant-initiated withdrawal.

    ``actor`` must be the application's own ``applicant``; callers are
    responsible for that authorization check (typically a permission class).
    The state machine still enforces that withdrawal is reachable from the
    current status (not from a terminal state).
    """
    validate_transition(application.status, ApplicationStatus.WITHDRAWN)
    application.status = ApplicationStatus.WITHDRAWN
    _stamp_decision(application, actor=actor, note=note)
    application.save(
        update_fields=[
            "status",
            "decision_note",
            "decided_by",
            "decided_at",
            "updated_at",
        ]
    )
    _notify_applicant(application, new_status=ApplicationStatus.WITHDRAWN)
    return application


# ── Permission helpers (used by core/permissions.py) ────────────────────────


def can_review_application(user, application: Application) -> bool:
    """HR/admin, the listing creator, and the department managers can review."""
    from core.permissions import _get_user_profile
    from core.services.document_service import is_hr_or_admin

    if not getattr(user, "is_authenticated", False):
        return False
    if is_hr_or_admin(user):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    listing = application.listing
    if listing is None:
        return False

    # The HR who posted the role retains ownership of the funnel.
    if listing.created_by_id and listing.created_by_id == profile.id:
        return True

    # Anyone who manages at least one employee in the hiring department
    # counts as a reviewer for that listing's funnel.
    department = listing.department
    if department is not None:
        if profile.direct_reports.filter(department_fk=department).exists():
            return True

    return False
