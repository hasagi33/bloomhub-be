"""
Leave Management Business Logic Service

Handles complex validation, calculations, and operations for leave management.
Multi-level approval workflow: Employee → Tech Lead → HR
"""

from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.enums import ProjectAssignmentStatus
from core.models import (
    LeaveAdjustment,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    Notification,
    ProjectAssignment,
    UserProfile,
    initialize_leave_balances_for_profile,
)
from core.permissions import _has_permission
from core.services.notification_service import create_notification

VACATIONS_MODULE = "Vacations"
APPROVE_REQUEST_ACTIONS = [
    "approve_team_requests",
    "override_requests",
    "adjust_balances",
]
HR_APPROVE_ACTIONS = ["adjust_balances", "override_requests", "configure_leave_types"]
ADJUST_BALANCES_ACTIONS = ["adjust_balances"]
CONFIGURE_LEAVE_TYPES_ACTIONS = ["configure_leave_types"]


def _notify_employee_leave_decision(
    leave_request: LeaveRequest, *, approved: bool, stage: str
) -> None:
    """Create an in-app notification for the employee's leave decision."""
    stage_label = "Tech Lead" if stage == "lead" else "HR"
    request_label = leave_request.get_leave_type_display()
    period = f"{leave_request.start_date} to {leave_request.end_date}"

    if approved:
        title = f"Leave request approved by {stage_label}"
        message = (
            f"Your {request_label} request for {period} was approved by your "
            f"{stage_label}."
        )
        if stage == "lead":
            message += " It is now awaiting HR review."
        notif_type = Notification.Type.SUCCESS
    else:
        title = f"Leave request rejected by {stage_label}"
        message = (
            f"Your {request_label} request for {period} was rejected by your "
            f"{stage_label}."
        )
        if leave_request.rejection_reason:
            message += f" Reason: {leave_request.rejection_reason}"
        notif_type = Notification.Type.WARNING

    create_notification(
        recipient=leave_request.employee,
        title=title,
        message=message,
        module=Notification.Module.VACATIONS,
        type=notif_type,
        link=f"/leave-requests/{leave_request.id}",
        metadata={
            "leave_request_id": leave_request.id,
            "leave_type": leave_request.leave_type,
            "status": leave_request.status,
            "stage": stage,
            "approved": approved,
        },
    )


def get_team_members_for_employee(employee: UserProfile):
    """Return active UserProfiles sharing at least one active project assignment with the employee, excluding self."""
    active_project_ids = ProjectAssignment.objects.filter(
        user_profile=employee,
        status=ProjectAssignmentStatus.ACTIVE,
    ).values_list("project_id", flat=True)

    return (
        UserProfile.objects.filter(
            is_active=True,
            project_assignments__project_id__in=active_project_ids,
            project_assignments__status=ProjectAssignmentStatus.ACTIVE,
        )
        .exclude(id=employee.id)
        .distinct()
        .select_related("user")
        .order_by("user__first_name", "user__last_name")
    )


def get_vacation_capabilities(user) -> dict:
    """Return per-feature capability booleans for the Vacations module."""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return {
            "can_approve_requests": True,
            "can_hr_approve": True,
            "can_adjust_balances": True,
            "can_configure_leave_types": True,
        }
    return {
        "can_approve_requests": _has_permission(
            user, VACATIONS_MODULE, APPROVE_REQUEST_ACTIONS
        ),
        "can_hr_approve": _has_permission(user, VACATIONS_MODULE, HR_APPROVE_ACTIONS),
        "can_adjust_balances": _has_permission(
            user, VACATIONS_MODULE, ADJUST_BALANCES_ACTIONS
        ),
        "can_configure_leave_types": _has_permission(
            user, VACATIONS_MODULE, CONFIGURE_LEAVE_TYPES_ACTIONS
        ),
    }


def calculate_working_days(start_date: date, end_date: date) -> int:
    """
    Calculate number of working days between two dates (excluding weekends).
    """
    if not start_date or not end_date or start_date > end_date:
        return 0

    current = start_date
    count = 0

    while current <= end_date:
        if current.weekday() < 5:  # Monday–Friday
            count += 1
        current += timedelta(days=1)

    return count


def calculate_calendar_days(start_date: date, end_date: date) -> int:
    """Calculate inclusive calendar-day span between two dates."""
    if not start_date or not end_date or start_date > end_date:
        return 0
    return (end_date - start_date).days + 1


def validate_leave_request(
    employee: UserProfile,
    leave_type: str,
    start_date: date,
    end_date: date,
    covering_employee: UserProfile | None = None,
    exclude_request_id: int | None = None,
) -> tuple[bool, str | None]:
    """
    Validate a leave request against all business rules.

    Returns:
        Tuple of (is_valid, error_message)
    """

    if start_date > end_date:
        return False, "End date must be after start date."

    if start_date < date.today():
        return False, "Start date cannot be in the past."

    overlapping = LeaveRequest.objects.filter(
        employee=employee,
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).exclude(status__in=[LeaveRequest.Status.REJECTED, LeaveRequest.Status.CANCELLED])

    if exclude_request_id:
        overlapping = overlapping.exclude(id=exclude_request_id)

    if overlapping.exists():
        return (
            False,
            "You already have an approved or pending leave request during this period.",
        )

    try:
        policy = LeavePolicy.objects.get(leave_type=leave_type)
    except LeavePolicy.DoesNotExist:
        return False, f"Leave policy for {leave_type} not found."

    if policy.min_notice_in_days > 0:
        notice_days = (start_date - date.today()).days
        if notice_days < policy.min_notice_in_days:
            return (
                False,
                f"This leave type requires at least {policy.min_notice_in_days} days notice.",
            )

    if policy.requires_covering_employee and not covering_employee:
        return False, "This leave type requires a covering employee."

    if policy.max_consecutive_days:
        days = calculate_working_days(start_date, end_date)
        if days > policy.max_consecutive_days:
            return (
                False,
                f"This leave type allows maximum {policy.max_consecutive_days} consecutive days.",
            )

    current_year = datetime.now().year
    try:
        balance = LeaveBalance.objects.get(
            employee=employee, leave_type=leave_type, year=current_year
        )
        requested_days = calculate_working_days(start_date, end_date)
        if balance.remaining < requested_days:
            return (
                False,
                f"Insufficient leave balance. You have {balance.remaining} days remaining, "
                f"but requesting {requested_days} days.",
            )
    except LeaveBalance.DoesNotExist:
        return (
            False,
            f"Leave balance for {leave_type} not found for year {current_year}.",
        )

    return True, None


@transaction.atomic
def approve_leave_request_lead(
    leave_request: LeaveRequest,
    approver: UserProfile,
    comments: str = "",
) -> tuple[bool, str | None]:
    """
    Tech Lead first-level approval: PENDING → LEAD_APPROVED.

    Sends:
      - Confirmation email to Tech Lead
      - Decision notification to employee
      - Forwarded request email to HR
    """
    if leave_request.status != LeaveRequest.Status.PENDING:
        return False, "Only pending requests can be approved by a Tech Lead."

    leave_request.status = LeaveRequest.Status.LEAD_APPROVED
    leave_request.lead_approver = approver
    leave_request.lead_approved_date = timezone.now()
    leave_request.lead_approval_comments = comments
    leave_request.save()

    # Emails (non-blocking — failures are logged, not raised)
    from core.services.mail.leave_notifications import (
        notify_approver_confirmation,
        notify_employee_lead_decision,
        notify_hr_lead_approved,
    )

    notify_approver_confirmation(leave_request, approver, approved=True, stage="lead")
    notify_employee_lead_decision(leave_request, approved=True)
    _notify_employee_leave_decision(leave_request, approved=True, stage="lead")
    notify_hr_lead_approved(leave_request)

    return True, None


@transaction.atomic
def approve_leave_request_hr(
    leave_request: LeaveRequest,
    approver: UserProfile,
    comments: str = "",
) -> tuple[bool, str | None]:
    """
    HR final approval: LEAD_APPROVED → APPROVED + balance deduction.

    Sends:
      - Confirmation email to HR
      - Final decision notification to employee
    """
    if leave_request.status != LeaveRequest.Status.LEAD_APPROVED:
        return False, "Only lead-approved requests can be given final approval by HR."

    current_year = datetime.now().year
    try:
        balance = LeaveBalance.objects.get(
            employee=leave_request.employee,
            leave_type=leave_request.leave_type,
            year=current_year,
        )
    except LeaveBalance.DoesNotExist:
        return False, "Leave balance not found."

    requested_days = calculate_calendar_days(
        leave_request.start_date, leave_request.end_date
    )
    if balance.remaining < requested_days:
        return (
            False,
            f"Insufficient leave balance. Employee has {balance.remaining} days remaining.",
        )

    leave_request.status = LeaveRequest.Status.APPROVED
    leave_request.approver = approver
    leave_request.approved_date = timezone.now()
    leave_request.approval_comments = comments
    leave_request.save()

    balance.used += requested_days
    balance.save()

    from core.services.mail.leave_notifications import (
        notify_approver_confirmation,
        notify_employee_hr_decision,
    )

    notify_approver_confirmation(leave_request, approver, approved=True, stage="hr")
    notify_employee_hr_decision(leave_request, approved=True)
    _notify_employee_leave_decision(leave_request, approved=True, stage="hr")

    from core.services.tempo_absence_sync_service import enqueue_leave_sync_on_commit

    enqueue_leave_sync_on_commit(leave_request.id)

    return True, None


@transaction.atomic
def reject_leave_request(
    leave_request: LeaveRequest,
    approver: UserProfile,
    reason: str,
) -> tuple[bool, str | None]:
    """
    Reject at either stage (PENDING by Tech Lead, or LEAD_APPROVED by HR).

    Sends:
      - Confirmation email to the rejector
      - Decision notification to employee (with appropriate stage context)
    """
    rejectable = {LeaveRequest.Status.PENDING, LeaveRequest.Status.LEAD_APPROVED}
    if leave_request.status not in rejectable:
        return False, "Only pending or lead-approved requests can be rejected."

    stage = "lead" if leave_request.status == LeaveRequest.Status.PENDING else "hr"

    leave_request.status = LeaveRequest.Status.REJECTED
    leave_request.approver = approver
    leave_request.approved_date = timezone.now()
    leave_request.rejection_reason = reason
    leave_request.save()

    from core.services.mail.leave_notifications import (
        notify_approver_confirmation,
        notify_employee_hr_decision,
        notify_employee_lead_decision,
    )

    notify_approver_confirmation(leave_request, approver, approved=False, stage=stage)

    if stage == "lead":
        notify_employee_lead_decision(leave_request, approved=False)
    else:
        notify_employee_hr_decision(leave_request, approved=False)
    _notify_employee_leave_decision(leave_request, approved=False, stage=stage)

    return True, None


@transaction.atomic
def cancel_leave_request(leave_request: LeaveRequest) -> tuple[bool, str | None]:
    """
    Cancel a leave request and restore balance if it was already approved.
    """
    if leave_request.status in [
        LeaveRequest.Status.REJECTED,
        LeaveRequest.Status.CANCELLED,
    ]:
        return False, f"Cannot cancel a {leave_request.status} request."

    if leave_request.status == LeaveRequest.Status.APPROVED:
        current_year = datetime.now().year
        try:
            balance = LeaveBalance.objects.get(
                employee=leave_request.employee,
                leave_type=leave_request.leave_type,
                year=current_year,
            )
            balance.used = max(
                0,
                balance.used
                - calculate_calendar_days(
                    leave_request.start_date, leave_request.end_date
                ),
            )
            balance.save()
        except LeaveBalance.DoesNotExist:
            pass

    leave_request.status = LeaveRequest.Status.CANCELLED
    leave_request.save()

    from core.services.tempo_absence_sync_service import enqueue_leave_sync_on_commit

    enqueue_leave_sync_on_commit(leave_request.id)

    return True, None


@transaction.atomic
def adjust_leave_balance(
    employee: UserProfile,
    leave_type: str,
    new_allocated: int,
    reason: str,
    adjusted_by: UserProfile,
    year: int | None = None,
) -> tuple[bool, str | None, LeaveBalance | None]:
    """Adjust employee leave balance (admin function)."""

    if year is None:
        year = datetime.now().year

    balance, created = LeaveBalance.objects.get_or_create(
        employee=employee,
        leave_type=leave_type,
        year=year,
        defaults={"allocated": new_allocated, "used": 0, "carryover": 0},
    )

    old_allocated = balance.allocated
    balance.allocated = new_allocated
    balance.save()

    LeaveAdjustment.objects.create(
        employee=employee,
        leave_type=leave_type,
        old_allocated=old_allocated,
        new_allocated=new_allocated,
        reason=reason,
        adjusted_by=adjusted_by,
    )

    return True, None, balance


def apply_carryover(
    employee: UserProfile, leave_type: str, from_year: int, to_year: int
) -> tuple[bool, str | None]:
    """Apply carryover from one year to the next."""

    try:
        policy = LeavePolicy.objects.get(leave_type=leave_type)
    except LeavePolicy.DoesNotExist:
        return False, f"Leave policy for {leave_type} not found."

    if policy.carryover_days == 0:
        return True, None

    try:
        source_balance = LeaveBalance.objects.get(
            employee=employee, leave_type=leave_type, year=from_year
        )
    except LeaveBalance.DoesNotExist:
        return False, f"Balance for {leave_type} in year {from_year} not found."

    remaining = source_balance.remaining
    carryover_amount = min(remaining, policy.carryover_days)

    if carryover_amount <= 0:
        return True, None

    target_balance, created = LeaveBalance.objects.get_or_create(
        employee=employee,
        leave_type=leave_type,
        year=to_year,
        defaults={
            "allocated": policy.allocated_days_per_year,
            "used": 0,
            "carryover": carryover_amount,
        },
    )

    if not created:
        target_balance.carryover = carryover_amount
        target_balance.save()

    return True, None


def initialize_leave_balances_for_employee(
    employee: UserProfile, year: int | None = None
) -> None:
    """Initialize leave balances for all leave types for an employee."""
    initialize_leave_balances_for_profile(employee, year)
