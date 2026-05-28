from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.enums import LeaveType, ProjectAssignmentStatus
from core.models import (
    LeaveAdjustment,
    LeaveBalance,
    LeavePolicy,
    LeaveRequest,
    Project,
    ProjectAssignment,
)
from core.services import leave_service


def _create_user(username: str, email: str | None = None):
    user = User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password="x"
    )
    return user, user.profile


def _setup_leave(
    employee_profile, *, leave_type=LeaveType.VACATION, allocated=10, used=0
):
    LeavePolicy.objects.update_or_create(
        leave_type=leave_type,
        defaults={
            "allocated_days_per_year": allocated,
            "carryover_days": 5,
            "requires_approval": True,
            "requires_covering_employee": False,
            "min_notice_in_days": 2,
            "max_consecutive_days": 10,
        },
    )
    balance, _ = LeaveBalance.objects.update_or_create(
        employee=employee_profile,
        leave_type=leave_type,
        year=timezone.now().year,
        defaults={"allocated": allocated, "used": used, "carryover": 0},
    )
    return balance


@pytest.mark.django_db
def test_calculate_and_validate_leave_request_branches():
    employee, profile = _create_user("leave-user")
    _setup_leave(profile)

    today = date.today()
    monday = date(2026, 5, 25)
    assert leave_service.calculate_working_days(monday, monday + timedelta(days=4)) == 5
    assert (
        leave_service.calculate_working_days(
            today + timedelta(days=2), today + timedelta(days=1)
        )
        == 0
    )

    ok, msg = leave_service.validate_leave_request(
        profile, LeaveType.VACATION, today - timedelta(days=1), today
    )
    assert ok is False and "past" in msg.lower()

    ok, msg = leave_service.validate_leave_request(
        profile,
        LeaveType.VACATION,
        today + timedelta(days=1),
        today + timedelta(days=3),
    )
    assert ok is False and "notice" in msg.lower()

    ok, msg = leave_service.validate_leave_request(
        profile, "missing", today + timedelta(days=3), today + timedelta(days=4)
    )
    assert ok is False and "not found" in msg.lower()

    covering, _ = _create_user("cover")
    LeavePolicy.objects.filter(leave_type=LeaveType.VACATION).update(
        requires_covering_employee=True
    )
    ok, msg = leave_service.validate_leave_request(
        profile,
        LeaveType.VACATION,
        today + timedelta(days=5),
        today + timedelta(days=8),
    )
    assert ok is False and "covering employee" in msg.lower()

    ok, msg = leave_service.validate_leave_request(
        profile,
        LeaveType.VACATION,
        today + timedelta(days=5),
        today + timedelta(days=20),
        covering_employee=covering,
    )
    assert ok is False and "maximum" in msg.lower()

    LeavePolicy.objects.filter(leave_type=LeaveType.VACATION).update(
        requires_covering_employee=False, min_notice_in_days=0
    )
    LeaveBalance.objects.filter(employee=profile, leave_type=LeaveType.VACATION).update(
        allocated=1, used=1
    )
    ok, msg = leave_service.validate_leave_request(
        profile,
        LeaveType.VACATION,
        today + timedelta(days=6),
        today + timedelta(days=6),
    )
    assert ok is False and "insufficient leave balance" in msg.lower()

    LeaveBalance.objects.filter(employee=profile, leave_type=LeaveType.VACATION).update(
        allocated=10, used=0
    )
    request = LeaveRequest.objects.create(
        employee=profile,
        leave_type=LeaveType.VACATION,
        start_date=today + timedelta(days=5),
        end_date=today + timedelta(days=7),
        reason="vacation",
    )
    ok, msg = leave_service.validate_leave_request(
        profile,
        LeaveType.VACATION,
        today + timedelta(days=5),
        today + timedelta(days=7),
    )
    assert ok is False and "already have" in msg.lower()
    request.status = LeaveRequest.Status.REJECTED
    request.save(update_fields=["status"])
    ok, msg = leave_service.validate_leave_request(
        profile,
        LeaveType.VACATION,
        today + timedelta(days=5),
        today + timedelta(days=7),
    )
    assert ok is True and msg is None


def _next_monday_at_least(min_offset: int) -> date:
    candidate = date.today() + timedelta(days=min_offset)
    return candidate + timedelta(days=(7 - candidate.weekday()) % 7)


@pytest.mark.django_db
def test_leave_approval_rejection_and_balance_changes(monkeypatch):
    employee, emp_profile = _create_user("employee")
    lead_user, lead_profile = _create_user("lead")
    hr_user, hr_profile = _create_user("hr")
    _setup_leave(emp_profile, used=2)

    request_start = _next_monday_at_least(7)
    request = LeaveRequest.objects.create(
        employee=emp_profile,
        leave_type=LeaveType.VACATION,
        start_date=request_start,
        end_date=request_start + timedelta(days=2),
        reason="holiday",
    )
    Project.objects.create(name="Proj", project_type="internal", status="active")

    calls = []
    monkeypatch.setattr(
        "core.services.mail.leave_notifications.notify_approver_confirmation",
        lambda *a, **k: calls.append(("conf", a, k)),
    )
    monkeypatch.setattr(
        "core.services.mail.leave_notifications.notify_employee_lead_decision",
        lambda *a, **k: calls.append(("lead", a, k)),
    )
    monkeypatch.setattr(
        "core.services.mail.leave_notifications.notify_hr_lead_approved",
        lambda *a, **k: calls.append(("hr", a, k)),
    )
    monkeypatch.setattr(
        "core.services.mail.leave_notifications.notify_employee_hr_decision",
        lambda *a, **k: calls.append(("hr_dec", a, k)),
    )

    ok, msg = leave_service.approve_leave_request_lead(request, lead_profile, "ok")
    assert ok is True and msg is None
    request.refresh_from_db()
    assert request.status == LeaveRequest.Status.LEAD_APPROVED

    ok, msg = leave_service.approve_leave_request_hr(request, hr_profile, "fine")
    assert ok is True and msg is None
    request.refresh_from_db()
    assert request.status == LeaveRequest.Status.APPROVED
    balance = LeaveBalance.objects.get(
        employee=emp_profile, leave_type=LeaveType.VACATION, year=timezone.now().year
    )
    assert balance.used == 5
    assert calls

    request2_start = _next_monday_at_least(14)
    request2 = LeaveRequest.objects.create(
        employee=emp_profile,
        leave_type=LeaveType.VACATION,
        start_date=request2_start,
        end_date=request2_start + timedelta(days=1),
        reason="retry",
    )
    ok, msg = leave_service.reject_leave_request(request2, lead_profile, "nope")
    assert ok is True and msg is None
    request2.refresh_from_db()
    assert request2.status == LeaveRequest.Status.REJECTED

    request.status = LeaveRequest.Status.APPROVED
    request.save(update_fields=["status"])
    balance.refresh_from_db()
    ok, msg = leave_service.cancel_leave_request(request)
    assert ok is True and msg is None
    request.refresh_from_db()
    balance.refresh_from_db()
    assert request.status == LeaveRequest.Status.CANCELLED
    assert balance.used == 2


@pytest.mark.django_db
def test_leave_adjustment_carryover_and_team_members(monkeypatch):
    employee, emp_profile = _create_user("employee2")
    teammate, team_profile = _create_user("teammate")
    other, other_profile = _create_user("other")
    project = Project.objects.create(
        name="Shared", project_type="internal", status="active"
    )
    ProjectAssignment.objects.create(
        user_profile=emp_profile,
        project=project,
        status=ProjectAssignmentStatus.ACTIVE,
        start_date=date.today() - timedelta(days=30),
    )
    ProjectAssignment.objects.create(
        user_profile=team_profile,
        project=project,
        status=ProjectAssignmentStatus.ACTIVE,
        start_date=date.today() - timedelta(days=10),
    )
    ProjectAssignment.objects.create(
        user_profile=other_profile,
        project=project,
        status=ProjectAssignmentStatus.COMPLETED,
        start_date=date.today() - timedelta(days=10),
        end_date=date.today() - timedelta(days=5),
    )

    members = leave_service.get_team_members_for_employee(emp_profile)
    assert [member.pk for member in members] == [team_profile.pk]

    monkeypatch.setattr(
        leave_service,
        "_has_permission",
        lambda user, module, actions: actions == ["adjust_balances"],
    )
    assert leave_service.get_vacation_capabilities(
        SimpleNamespace(is_staff=False, is_superuser=False)
    ) == {
        "can_approve_requests": False,
        "can_hr_approve": False,
        "can_adjust_balances": True,
        "can_configure_leave_types": False,
    }

    _setup_leave(emp_profile, allocated=8, used=1)
    ok, msg, balance = leave_service.adjust_leave_balance(
        emp_profile,
        LeaveType.VACATION,
        12,
        "bonus",
        team_profile,
        year=timezone.now().year,
    )
    assert ok is True and msg is None and balance.allocated == 12
    assert LeaveAdjustment.objects.filter(employee=emp_profile).count() == 1

    LeavePolicy.objects.filter(leave_type=LeaveType.VACATION).update(
        carryover_days=3, allocated_days_per_year=10
    )
    LeaveBalance.objects.filter(
        employee=emp_profile, leave_type=LeaveType.VACATION, year=timezone.now().year
    ).update(used=4)
    ok, msg = leave_service.apply_carryover(
        emp_profile, LeaveType.VACATION, timezone.now().year, timezone.now().year + 1
    )
    assert ok is True and msg is None
    next_balance = LeaveBalance.objects.get(
        employee=emp_profile,
        leave_type=LeaveType.VACATION,
        year=timezone.now().year + 1,
    )
    assert next_balance.carryover == 3

    leave_service.initialize_leave_balances_for_employee(
        other_profile, year=timezone.now().year + 2
    )
    assert LeaveBalance.objects.filter(
        employee=other_profile, year=timezone.now().year + 2
    ).exists()
