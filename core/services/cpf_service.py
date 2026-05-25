"""Career Progression Framework (CPF) advancement-tracking business logic."""

from django.db import transaction

from core.enums import (
    CPFChangeSource,
    CPFProgressionEventType,
    ReviewStatus,
    TrackedField,
)
from core.models import CPFLevelChange, PerformanceReview, UserProfile
from core.services.profile_change_history import log_employee_profile_change


def _employee_level_changes(employee: UserProfile):
    return CPFLevelChange.objects.filter(employee=employee)


def _employee_cpf_reviews(employee: UserProfile):
    """Completed performance reviews that carry CPF assessment data."""
    return PerformanceReview.objects.filter(
        employee=employee,
        status=ReviewStatus.COMPLETED,
    ).exclude(cpf_recommended_level="", cpf_score__isnull=True)


def _level_change_event(change: CPFLevelChange) -> dict:
    return {
        "date": change.effective_date,
        "event_type": CPFProgressionEventType.LEVEL_CHANGE.value,
        "previous_level": change.previous_level,
        "new_level": change.new_level,
        "source": change.source,
        "cpf_score": change.cpf_score,
        "notes": change.notes,
        "reference_id": change.id,
        "reference_label": change.get_source_display(),
    }


def _review_event(review: PerformanceReview) -> dict:
    review_date = (
        review.completed_at.date() if review.completed_at else review.scheduled_date
    )
    return {
        "date": review_date,
        "event_type": CPFProgressionEventType.REVIEW_ASSESSMENT.value,
        "previous_level": review.cpf_current_level,
        "new_level": review.cpf_recommended_level,
        "source": CPFChangeSource.PERFORMANCE_REVIEW.value,
        "cpf_score": review.cpf_score,
        "notes": review.summary,
        "reference_id": review.id,
        "reference_label": (
            review.get_outcome_display()
            if review.outcome
            else (review.title or "Performance Review")
        ),
    }


def build_cpf_progression(employee: UserProfile) -> dict:
    """Return a consolidated CPF career-progression timeline for an employee.

    Merges recorded CPF level changes with completed performance reviews that
    carry CPF data (review outcomes), sorted chronologically — suitable for
    rendering a career-progression timeline visualization.
    """
    events = [
        _level_change_event(change) for change in _employee_level_changes(employee)
    ]
    events += [_review_event(review) for review in _employee_cpf_reviews(employee)]
    events.sort(key=lambda event: (event["date"], event["event_type"]))
    return {
        "employee_id": employee.id,
        "employee_name": employee.user.get_full_name(),
        "current_level": employee.cpf_level or "",
        "timeline": events,
    }


@transaction.atomic
def sync_employee_current_cpf_level(employee: UserProfile, *, actor=None) -> None:
    """Set the employee's profile CPF level to their latest recorded change.

    The current level is the new level of the most recent change by effective
    date. ``cpf_level`` is a tracked profile field, so the update is also
    written to the employee profile change history. Call this after a CPF
    level change is created, edited, or removed.
    """
    latest = (
        CPFLevelChange.objects.filter(employee=employee)
        .order_by("-effective_date", "-created_at")
        .first()
    )
    if latest is None:
        return
    old_level = employee.cpf_level or ""
    if old_level == latest.new_level:
        return
    employee.cpf_level = latest.new_level
    employee.save(update_fields=["cpf_level"])
    log_employee_profile_change(
        employee=employee,
        field=TrackedField.CPF_LEVEL,
        old_value=old_level,
        new_value=latest.new_level,
        changed_by=actor,
    )
