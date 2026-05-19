"""
Project Management business logic.

Visibility, filtering, archive/reactivate transitions for Project records.
Mirrors the role-resolution pattern from ``document_service``.
"""

from __future__ import annotations

from datetime import date, datetime

from django.db.models import Count, Q, QuerySet

from core.enums import ProjectAssignmentStatus, ProjectStatus
from core.models import Project, ProjectAssignment, UserProfile

# Role values match Role.name lowercase, mirroring document_service resolution.
ROLE_ADMIN = "admin"
ROLE_HR = "hr"
ROLE_MANAGER = "manager"
ROLE_EMPLOYEE = "employee"

WRITE_ROLES = {ROLE_ADMIN, ROLE_HR}


class ProjectFilterError(ValueError):
    """Raised when a list filter value cannot be parsed."""


# ──────────────────────────────────────────
# Role resolution
# ──────────────────────────────────────────


def _get_profile(user) -> UserProfile | None:
    try:
        return user.profile
    except Exception:
        return None


def _profile_role_name(profile: UserProfile | None) -> str:
    role = getattr(profile, "role", None)
    return (getattr(role, "name", "") or "").lower()


def is_admin_or_hr(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    role_name = _profile_role_name(_get_profile(user))
    return role_name == ROLE_ADMIN or role_name.startswith(ROLE_HR)


def is_manager(user) -> bool:
    profile = _get_profile(user)
    if profile is None:
        return False
    if _profile_role_name(profile) == ROLE_MANAGER:
        return True
    return profile.direct_reports.exists()


# ──────────────────────────────────────────
# Visibility
# ──────────────────────────────────────────


def visible_projects_for(user) -> QuerySet[Project]:
    """Return Project queryset the user is allowed to see."""
    if is_admin_or_hr(user):
        return Project.objects.all()

    profile = _get_profile(user)
    if profile is None:
        return Project.objects.none()

    if is_manager(user):
        # Owned projects + projects the manager is assigned to.
        return Project.objects.filter(
            Q(owner_id=profile.id) | Q(assignments__user_profile_id=profile.id)
        ).distinct()

    # Employee: only projects they are assigned to.
    return Project.objects.filter(assignments__user_profile_id=profile.id).distinct()


def can_view_project(user, project: Project) -> bool:
    return visible_projects_for(user).filter(pk=project.pk).exists()


def can_modify_projects(user) -> bool:
    return is_admin_or_hr(user)


# ──────────────────────────────────────────
# Filtering
# ──────────────────────────────────────────


def _parse_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ProjectFilterError(
            f"Invalid date for '{field}'. Expected YYYY-MM-DD."
        ) from exc


def _parse_int(value: str, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectFilterError(f"Invalid integer for '{field}'.") from exc


def apply_project_filters(queryset: QuerySet[Project], params) -> QuerySet[Project]:
    """Apply list filters from query params. Raises ProjectFilterError on bad input."""
    search = (params.get("search") or "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(client__icontains=search)
        )

    status_value = params.get("status")
    if status_value:
        valid = {choice for choice, _ in ProjectStatus.choices}
        if status_value not in valid:
            raise ProjectFilterError(f"Invalid status. Allowed: {sorted(valid)}.")
        queryset = queryset.filter(status=status_value)

    owner = params.get("owner") or params.get("owner_id")
    if owner:
        queryset = queryset.filter(owner_id=_parse_int(owner, "owner"))

    active_from_raw = params.get("active_from")
    active_to_raw = params.get("active_to")
    if active_from_raw or active_to_raw:
        active_from = (
            _parse_date(active_from_raw, "active_from") if active_from_raw else None
        )
        active_to = _parse_date(active_to_raw, "active_to") if active_to_raw else None
        if active_from and active_to and active_to < active_from:
            raise ProjectFilterError("active_to cannot be before active_from.")
        # Project overlaps window if start <= window_end AND (end is null OR end >= window_start).
        if active_to is not None:
            queryset = queryset.filter(
                Q(start_date__lte=active_to) | Q(start_date__isnull=True)
            )
        if active_from is not None:
            queryset = queryset.filter(
                Q(end_date__gte=active_from) | Q(end_date__isnull=True)
            )

    return queryset


# ──────────────────────────────────────────
# Summary counts / active members
# ──────────────────────────────────────────


def annotate_assignment_counts(queryset: QuerySet[Project]) -> QuerySet[Project]:
    return queryset.annotate(
        total_assignments_count=Count("assignments", distinct=True),
        active_assignments_count=Count(
            "assignments",
            filter=Q(assignments__status=ProjectAssignmentStatus.ACTIVE),
            distinct=True,
        ),
        active_members_count=Count(
            "assignments__user_profile",
            filter=Q(assignments__status=ProjectAssignmentStatus.ACTIVE),
            distinct=True,
        ),
    )


def active_members_for(project: Project):
    """Return distinct active assignments with related profile/user pre-fetched."""
    return (
        ProjectAssignment.objects.select_related("user_profile__user")
        .filter(project=project, status=ProjectAssignmentStatus.ACTIVE)
        .order_by("user_profile__full_name")
    )


# ──────────────────────────────────────────
# Archive / reactivate
# ──────────────────────────────────────────


def archive_project(project: Project) -> Project:
    project.status = ProjectStatus.ARCHIVED
    project.save(update_fields=["status", "updated_at"])
    return project


def reactivate_project(project: Project) -> Project:
    project.status = ProjectStatus.ACTIVE
    project.save(update_fields=["status", "updated_at"])
    return project
