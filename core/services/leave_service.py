"""
Leave Management Business Logic Service

Handles complex validation, calculations, and operations for leave management.
"""

from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.models import (
    LeaveAdjustment,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    UserProfile,
)


def calculate_working_days(start_date: date, end_date: date) -> int:
    """
    Calculate number of working days between two dates (excluding weekends).

    Args:
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Number of working days (Monday-Friday)
    """
    if not start_date or not end_date or start_date > end_date:
        return 0

    current = start_date
    count = 0

    while current <= end_date:
        # 0 = Monday, 6 = Sunday
        if current.weekday() < 5:  # Monday to Friday
            count += 1
        current += timedelta(days=1)

    return count


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

    Args:
        employee: Employee requesting leave
        leave_type: Type of leave
        start_date: Leave start date
        end_date: Leave end date
        covering_employee: Optional covering employee
        exclude_request_id: Request ID to exclude from overlap check (for updates)

    Returns:
        Tuple of (is_valid, error_message)
    """

    # 1. Validate date range
    if start_date > end_date:
        return False, "End date must be after start date."

    # 2. Validate not in past
    if start_date < date.today():
        return False, "Start date cannot be in the past."

    # 3. Check for overlapping requests
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

    # 4. Get leave policy
    try:
        policy = LeavePolicy.objects.get(leave_type=leave_type)
    except LeavePolicy.DoesNotExist:
        return False, f"Leave policy for {leave_type} not found."

    # 5. Check minimum notice requirement
    if policy.min_notice_in_days > 0:
        notice_days = (start_date - date.today()).days
        if notice_days < policy.min_notice_in_days:
            return (
                False,
                f"This leave type requires at least {policy.min_notice_in_days} days notice.",
            )

    # 6. Check covering employee requirement
    if policy.requires_covering_employee and not covering_employee:
        return False, "This leave type requires a covering employee."

    # 7. Check max consecutive days
    if policy.max_consecutive_days:
        days = calculate_working_days(start_date, end_date)
        if days > policy.max_consecutive_days:
            return (
                False,
                f"This leave type allows maximum {policy.max_consecutive_days} consecutive days.",
            )

    # 8. Check sufficient balance
    current_year = datetime.now().year
    try:
        balance = LeaveBalance.objects.get(
            employee=employee, leave_type=leave_type, year=current_year
        )
        requested_days = calculate_working_days(start_date, end_date)
        if balance.remaining < requested_days:
            return (
                False,
                f"Insufficient leave balance. You have {balance.remaining} days remaining, but requesting {requested_days} days.",
            )
    except LeaveBalance.DoesNotExist:
        return (
            False,
            f"Leave balance for {leave_type} not found for year {current_year}.",
        )

    return True, None


@transaction.atomic
def approve_leave_request(
    leave_request: LeaveRequest, approver: UserProfile, comments: str = ""
) -> tuple[bool, str | None]:
    """
    Approve a leave request and deduct from balance.

    Args:
        leave_request: Leave request to approve
        approver: Manager approving the request
        comments: Optional approval comments

    Returns:
        Tuple of (success, error_message)
    """

    # Check if already approved
    if leave_request.status != LeaveRequest.Status.PENDING:
        return False, "Only pending requests can be approved."

    # Check sufficient balance again (in case it changed)
    current_year = datetime.now().year
    try:
        balance = LeaveBalance.objects.get(
            employee=leave_request.employee,
            leave_type=leave_request.leave_type,
            year=current_year,
        )
    except LeaveBalance.DoesNotExist:
        return False, "Leave balance not found."

    requested_days = leave_request.days
    if balance.remaining < requested_days:
        return (
            False,
            f"Insufficient leave balance. Employee has {balance.remaining} days remaining.",
        )

    # Update leave request
    leave_request.status = LeaveRequest.Status.APPROVED
    leave_request.approver = approver
    leave_request.approved_date = timezone.now()
    leave_request.approval_comments = comments
    leave_request.save()

    # Deduct from balance
    balance.used += requested_days
    balance.save()

    return True, None


@transaction.atomic
def reject_leave_request(
    leave_request: LeaveRequest, approver: UserProfile, reason: str
) -> tuple[bool, str | None]:
    """
    Reject a leave request.

    Args:
        leave_request: Leave request to reject
        approver: Manager rejecting the request
        reason: Rejection reason

    Returns:
        Tuple of (success, error_message)
    """

    # Check if already processed
    if leave_request.status != LeaveRequest.Status.PENDING:
        return False, "Only pending requests can be rejected."

    # Update leave request
    leave_request.status = LeaveRequest.Status.REJECTED
    leave_request.approver = approver
    leave_request.approved_date = timezone.now()
    leave_request.rejection_reason = reason
    leave_request.save()

    return True, None


@transaction.atomic
def cancel_leave_request(leave_request: LeaveRequest) -> tuple[bool, str | None]:
    """
    Cancel a leave request and restore balance if approved.

    Args:
        leave_request: Leave request to cancel

    Returns:
        Tuple of (success, error_message)
    """

    # Cannot cancel rejected or already cancelled requests
    if leave_request.status in [
        LeaveRequest.Status.REJECTED,
        LeaveRequest.Status.CANCELLED,
    ]:
        return False, f"Cannot cancel a {leave_request.status} request."

    # If approved, restore the balance
    if leave_request.status == LeaveRequest.Status.APPROVED:
        current_year = datetime.now().year
        try:
            balance = LeaveBalance.objects.get(
                employee=leave_request.employee,
                leave_type=leave_request.leave_type,
                year=current_year,
            )

            # Restore days
            days_to_restore = leave_request.days
            balance.used = max(0, balance.used - days_to_restore)
            balance.save()
        except LeaveBalance.DoesNotExist:
            pass  # Balance not found, skip restoration

    # Update status
    leave_request.status = LeaveRequest.Status.CANCELLED
    leave_request.save()

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
    """
    Adjust employee leave balance (admin function).

    Args:
        employee: Employee whose balance to adjust
        leave_type: Type of leave
        new_allocated: New allocated days
        reason: Reason for adjustment
        adjusted_by: Admin making the adjustment
        year: Year (defaults to current year)

    Returns:
        Tuple of (success, error_message, updated_balance)
    """

    if year is None:
        year = datetime.now().year

    # Get or create balance
    balance, created = LeaveBalance.objects.get_or_create(
        employee=employee,
        leave_type=leave_type,
        year=year,
        defaults={"allocated": new_allocated, "used": 0, "carryover": 0},
    )

    old_allocated = balance.allocated

    # Update allocated
    balance.allocated = new_allocated
    balance.save()

    # Create audit record
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
    """
    Apply carryover from one year to the next.

    Args:
        employee: Employee
        leave_type: Type of leave
        from_year: Source year
        to_year: Target year

    Returns:
        Tuple of (success, error_message)
    """

    # Get policy
    try:
        policy = LeavePolicy.objects.get(leave_type=leave_type)
    except LeavePolicy.DoesNotExist:
        return False, f"Leave policy for {leave_type} not found."

    if policy.carryover_days == 0:
        return True, None  # No carryover for this leave type

    # Get source balance
    try:
        source_balance = LeaveBalance.objects.get(
            employee=employee, leave_type=leave_type, year=from_year
        )
    except LeaveBalance.DoesNotExist:
        return False, f"Balance for {leave_type} in year {from_year} not found."

    # Calculate carryover amount
    remaining = source_balance.remaining
    carryover_amount = min(remaining, policy.carryover_days)

    if carryover_amount <= 0:
        return True, None  # Nothing to carry over

    # Get or create target balance
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
    """
    Initialize leave balances for all leave types for an employee.
    Useful for new employees or new year setup.

    Args:
        employee: Employee to initialize balances for
        year: Year (defaults to current year)
    """

    if year is None:
        year = datetime.now().year

    policies = LeavePolicy.objects.all()

    for policy in policies:
        LeaveBalance.objects.get_or_create(
            employee=employee,
            leave_type=policy.leave_type,
            year=year,
            defaults={
                "allocated": policy.allocated_days_per_year,
                "used": 0,
                "carryover": 0,
            },
        )
