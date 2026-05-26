import hashlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.enums import (
    ProjectAssignmentStatus,
    ProjectStatus,
    TimeEntryAuditEventType,
    TimeEntrySourceType,
    TimeEntryStatus,
)
from core.models import (
    Permission,
    ProjectAssignment,
    TimeEntry,
    TimeEntryAuditEvent,
    UserProfile,
)

TIME_TRACKING_MODULE = "Time Tracking"
DEFAULT_WEEKLY_CAPACITY_HOURS = Decimal("40.00")


def has_time_tracking_permission(user, feature_action: str) -> bool:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    try:
        profile = user.profile
    except Exception:
        return False
    try:
        permission = Permission.objects.get(
            module_name=TIME_TRACKING_MODULE,
            feature_action=feature_action,
        )
    except Permission.DoesNotExist:
        return False
    return profile.has_permission(permission)


def profile_for_user(user) -> UserProfile | None:
    try:
        return user.profile
    except Exception:
        return None


def can_view_time_entry(user, entry: TimeEntry) -> bool:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    profile = profile_for_user(user)
    if profile is None:
        return False
    if entry.employee_id == profile.id:
        return has_time_tracking_permission(user, "view_own_timesheet")
    if has_time_tracking_permission(user, "view_dept_timesheets"):
        return True
    if has_time_tracking_permission(user, "view_team_timesheets"):
        return entry.employee.managers.filter(pk=profile.pk).exists()
    return False


def can_edit_time_entry(user, entry: TimeEntry) -> bool:
    if entry.status == TimeEntryStatus.APPROVED:
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    profile = profile_for_user(user)
    if profile is None:
        return False
    if entry.source_type != TimeEntrySourceType.MANUAL:
        return can_approve_time_entry(user, entry)
    return (
        entry.employee_id == profile.id
        and has_time_tracking_permission(user, "view_own_timesheet")
        and entry.status
        in {
            TimeEntryStatus.DRAFT,
            TimeEntryStatus.REJECTED,
        }
    )


def can_delete_time_entry(user, entry: TimeEntry) -> bool:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    if entry.source_type != TimeEntrySourceType.MANUAL:
        return can_approve_time_entry(user, entry)
    return can_edit_time_entry(user, entry) and entry.status in {
        TimeEntryStatus.DRAFT,
        TimeEntryStatus.REJECTED,
    }


def can_approve_time_entry(user, entry: TimeEntry) -> bool:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    if not has_time_tracking_permission(user, "approve_team_timesheets"):
        return False
    profile = profile_for_user(user)
    if profile is None:
        return False
    return entry.employee.managers.filter(pk=profile.pk).exists()


def can_view_employee_timesheet(user, employee: UserProfile) -> bool:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    profile = profile_for_user(user)
    if profile is None:
        return False
    if employee.id == profile.id:
        return has_time_tracking_permission(user, "view_own_timesheet")
    if has_time_tracking_permission(user, "view_dept_timesheets"):
        return True
    if has_time_tracking_permission(user, "view_team_timesheets"):
        return employee.managers.filter(pk=profile.pk).exists()
    return False


def active_time_tracking_allocations(*, employee: UserProfile, work_date: date):
    assignments = (
        ProjectAssignment.objects.select_related("project")
        .filter(
            user_profile=employee,
            status=ProjectAssignmentStatus.ACTIVE,
            start_date__lte=work_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=work_date))
        .exclude(project__status=ProjectStatus.ARCHIVED)
        .order_by("project__name", "start_date")
    )
    rows = []
    total = Decimal("0.00")
    for assignment in assignments:
        allocation = Decimal(assignment.allocation_percentage)
        total += allocation
        rows.append(
            {
                "assignment_id": assignment.id,
                "project_id": assignment.project_id,
                "project_name": assignment.project.name,
                "allocation_percentage": str(allocation.quantize(Decimal("0.01"))),
                "planned_weekly_hours": str(
                    (
                        DEFAULT_WEEKLY_CAPACITY_HOURS * allocation / Decimal("100")
                    ).quantize(Decimal("0.01"))
                ),
                "start_date": assignment.start_date.isoformat(),
                "end_date": (
                    assignment.end_date.isoformat() if assignment.end_date else None
                ),
                "status": assignment.status,
            }
        )
    return {
        "employee_id": employee.id,
        "work_date": work_date.isoformat(),
        "total_allocation_percentage": str(total.quantize(Decimal("0.01"))),
        "remaining_allocation_percentage": str(
            max(Decimal("0.00"), Decimal("100.00") - total).quantize(Decimal("0.01"))
        ),
        "assignments": rows,
    }


def canonical_duplicate_fingerprint(
    *,
    employee_id: int,
    work_date: date,
    project_id: int,
    task_id: int | None,
    jira_issue_key: str,
    hours: Decimal,
    notes: str,
) -> str:
    normalized = "|".join(
        [
            str(employee_id),
            work_date.isoformat(),
            str(project_id),
            str(task_id or ""),
            jira_issue_key.strip().upper(),
            f"{Decimal(hours):.2f}",
            " ".join((notes or "").strip().split()).lower(),
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def fingerprint_for_entry(entry: TimeEntry) -> str:
    jira_issue_key = ""
    if entry.task_id and entry.task:
        jira_issue_key = entry.task.jira_issue_key
    if not jira_issue_key:
        jira_issue_key = str(entry.source_metadata.get("jira_issue_key", ""))
    return canonical_duplicate_fingerprint(
        employee_id=entry.employee_id,
        work_date=entry.work_date,
        project_id=entry.project_id,
        task_id=entry.task_id,
        jira_issue_key=jira_issue_key,
        hours=entry.hours,
        notes=entry.notes,
    )


def find_duplicate(entry: TimeEntry) -> TimeEntry | None:
    queryset = TimeEntry.objects.filter(
        duplicate_fingerprint=entry.duplicate_fingerprint
    )
    if entry.pk:
        queryset = queryset.exclude(pk=entry.pk)
    return queryset.order_by("created_at").first()


def log_time_entry_event(
    entry: TimeEntry,
    event_type: str,
    actor: UserProfile | None = None,
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> TimeEntryAuditEvent:
    return TimeEntryAuditEvent.objects.create(
        time_entry=entry,
        event_type=event_type,
        actor=actor,
        message=message,
        metadata=metadata or {},
    )


def _date_range(start: date, end: date) -> list[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _assignment_overlaps_range(
    assignment: ProjectAssignment,
    range_start: date,
    range_end: date,
) -> bool:
    if assignment.start_date > range_end:
        return False
    if assignment.end_date and assignment.end_date < range_start:
        return False
    return True


def weekly_allocation_summary(
    *,
    employee: UserProfile,
    week_start: date,
    weekly_capacity_hours: Decimal = DEFAULT_WEEKLY_CAPACITY_HOURS,
) -> dict[str, Any]:
    week_end = week_start + timedelta(days=6)
    days = _date_range(week_start, week_end)
    weekday_count = sum(1 for day in days if day.weekday() < 5) or 5
    daily_capacity = weekly_capacity_hours / Decimal(weekday_count)

    assignments = list(
        ProjectAssignment.objects.select_related("project")
        .filter(user_profile=employee)
        .filter(start_date__lte=week_end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=week_start))
        .order_by("project__name", "start_date")
    )

    project_rows: dict[int, dict[str, Any]] = {}
    for assignment in assignments:
        if not _assignment_overlaps_range(assignment, week_start, week_end):
            continue
        project = assignment.project
        row = project_rows.setdefault(
            project.id,
            {
                "project_id": project.id,
                "project_name": project.name,
                "planned_hours": Decimal("0.00"),
                "actual_hours": Decimal("0.00"),
                "allocation_percentage": Decimal("0.00"),
                "allocation_status": "allocated",
                "assignments": [],
            },
        )
        active_days = []
        for day in days:
            if day.weekday() >= 5:
                continue
            if day < assignment.start_date:
                continue
            if assignment.end_date and day > assignment.end_date:
                continue
            active_days.append(day)
        planned = (
            daily_capacity
            * Decimal(assignment.allocation_percentage)
            / Decimal("100")
            * Decimal(len(active_days))
        )
        row["planned_hours"] += planned
        row["allocation_percentage"] += (
            Decimal(assignment.allocation_percentage)
            * Decimal(len(active_days))
            / Decimal(weekday_count)
        )
        row["assignments"].append(
            {
                "assignment_id": assignment.id,
                "allocation_percentage": assignment.allocation_percentage,
                "start_date": assignment.start_date.isoformat(),
                "end_date": (
                    assignment.end_date.isoformat() if assignment.end_date else None
                ),
                "status": assignment.status,
                "active_weekdays": len(active_days),
            }
        )

    actuals = (
        TimeEntry.objects.filter(
            employee=employee,
            work_date__gte=week_start,
            work_date__lte=week_end,
        )
        .values("project_id", "project__name")
        .annotate(total_hours=Sum("hours"))
    )
    for actual in actuals:
        project_id = actual["project_id"]
        row = project_rows.setdefault(
            project_id,
            {
                "project_id": project_id,
                "project_name": actual["project__name"],
                "planned_hours": Decimal("0.00"),
                "actual_hours": Decimal("0.00"),
                "allocation_percentage": Decimal("0.00"),
                "allocation_status": "unallocated",
                "assignments": [],
            },
        )
        row["actual_hours"] = actual["total_hours"] or Decimal("0.00")
        if row["planned_hours"] == Decimal("0.00"):
            row["allocation_status"] = "unallocated"

    planned_total = sum(
        (row["planned_hours"] for row in project_rows.values()),
        Decimal("0.00"),
    )
    actual_total = sum(
        (row["actual_hours"] for row in project_rows.values()),
        Decimal("0.00"),
    )
    remaining_capacity = weekly_capacity_hours - planned_total
    unallocated_capacity = max(remaining_capacity, Decimal("0.00"))

    def money(value: Decimal) -> str:
        return str(value.quantize(Decimal("0.01")))

    projects = []
    for row in sorted(project_rows.values(), key=lambda item: item["project_name"]):
        projects.append(
            {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "planned_hours": money(row["planned_hours"]),
                "actual_hours": money(row["actual_hours"]),
                "variance_hours": money(row["actual_hours"] - row["planned_hours"]),
                "allocation_percentage": money(row["allocation_percentage"]),
                "allocation_status": row["allocation_status"],
                "assignments": row["assignments"],
            }
        )

    return {
        "employee_id": employee.id,
        "employee_name": employee.full_name or employee.user.username,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "weekly_capacity_hours": money(weekly_capacity_hours),
        "planned_hours": money(planned_total),
        "actual_hours": money(actual_total),
        "remaining_capacity_hours": money(weekly_capacity_hours - actual_total),
        "unallocated_capacity_hours": money(unallocated_capacity),
        "projects": projects,
    }


@transaction.atomic
def submit_entries_for_week(
    *,
    user,
    employee: UserProfile,
    week_start: date,
) -> list[TimeEntry]:
    profile = profile_for_user(user)
    if profile is None or (employee.id != profile.id and not user.is_staff):
        raise PermissionDenied("You can only submit your own timesheet.")
    week_end = week_start + timedelta(days=6)
    entries = list(
        TimeEntry.objects.select_for_update()
        .filter(
            employee=employee,
            work_date__gte=week_start,
            work_date__lte=week_end,
            status__in=[TimeEntryStatus.DRAFT, TimeEntryStatus.REJECTED],
        )
        .exclude(work_date__week_day__in=[1, 7])
    )
    now = timezone.now()
    for entry in entries:
        entry.status = TimeEntryStatus.SUBMITTED
        entry.submitted_at = now
        entry.submitted_by = profile
        entry.rejected_at = None
        entry.rejected_by = None
        entry.rejection_reason = ""
        entry.save(
            update_fields=[
                "status",
                "submitted_at",
                "submitted_by",
                "rejected_at",
                "rejected_by",
                "rejection_reason",
                "updated_at",
            ]
        )
        log_time_entry_event(entry, TimeEntryAuditEventType.SUBMITTED, profile)
    return entries


@transaction.atomic
def approve_entry(*, user, entry: TimeEntry) -> TimeEntry:
    if entry.status != TimeEntryStatus.SUBMITTED:
        raise ValidationError("Only submitted time entries can be approved.")
    if not can_approve_time_entry(user, entry):
        raise PermissionDenied("You do not have permission to approve this entry.")
    actor = profile_for_user(user)
    entry.status = TimeEntryStatus.APPROVED
    entry.approved_at = timezone.now()
    entry.approved_by = actor
    entry.rejected_at = None
    entry.rejected_by = None
    entry.rejection_reason = ""
    entry.save()
    log_time_entry_event(entry, TimeEntryAuditEventType.APPROVED, actor)
    return entry


@transaction.atomic
def reject_entry(*, user, entry: TimeEntry, reason: str) -> TimeEntry:
    if entry.status != TimeEntryStatus.SUBMITTED:
        raise ValidationError("Only submitted time entries can be rejected.")
    if not can_approve_time_entry(user, entry):
        raise PermissionDenied("You do not have permission to reject this entry.")
    if not reason.strip():
        raise ValidationError({"reason": "Rejection reason is required."})
    actor = profile_for_user(user)
    entry.status = TimeEntryStatus.REJECTED
    entry.rejected_at = timezone.now()
    entry.rejected_by = actor
    entry.rejection_reason = reason.strip()
    entry.approved_at = None
    entry.approved_by = None
    entry.save()
    log_time_entry_event(
        entry,
        TimeEntryAuditEventType.REJECTED,
        actor,
        metadata={"reason": entry.rejection_reason},
    )
    return entry
