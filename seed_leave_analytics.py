"""
Seed leave analytics data for dashboard overview.

Run with: python manage.py shell < seed_leave_analytics.py
"""

from datetime import date

from core.enums import LeaveRequestStatus, LeaveType
from core.models import LeaveRequest, UserProfile, ensure_default_leave_policies
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
    snapshot_leave_balances,
)

DEPARTMENTS = {
    "tarik": "Engineering",
    "tarxton": "Product",
    "rizvoni": "Sales",
    "ahmed_buric": "Drogerija",
}


def set_departments():
    updated = 0
    for username, dept in DEPARTMENTS.items():
        profile = UserProfile.objects.filter(user__username=username).first()
        if profile is None:
            continue
        if profile.department != dept:
            profile.department = dept
            profile.save(update_fields=["department"])
            updated += 1
    return updated


SEED_LEAVES = [
    (
        "tarik",
        LeaveType.VACATION,
        date(2026, 1, 5),
        date(2026, 1, 9),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarik",
        LeaveType.SICK,
        date(2026, 1, 20),
        date(2026, 1, 21),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarik",
        LeaveType.WFH,
        date(2026, 2, 9),
        date(2026, 2, 13),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarik",
        LeaveType.VACATION,
        date(2026, 3, 16),
        date(2026, 3, 20),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarik",
        LeaveType.PERSONAL,
        date(2026, 4, 6),
        date(2026, 4, 7),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarik",
        LeaveType.VACATION,
        date(2026, 6, 22),
        date(2026, 6, 26),
        LeaveRequestStatus.PENDING,
    ),
    (
        "ahmed_buric",
        LeaveType.VACATION,
        date(2026, 2, 2),
        date(2026, 2, 6),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "ahmed_buric",
        LeaveType.SICK,
        date(2026, 3, 11),
        date(2026, 3, 12),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "ahmed_buric",
        LeaveType.WFH,
        date(2026, 4, 13),
        date(2026, 4, 17),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "ahmed_buric",
        LeaveType.VACATION,
        date(2026, 5, 25),
        date(2026, 5, 29),
        LeaveRequestStatus.APPROVED,
    ),  # covers today
    (
        "ahmed_buric",
        LeaveType.VACATION,
        date(2026, 7, 6),
        date(2026, 7, 10),
        LeaveRequestStatus.PENDING,
    ),
    (
        "ahmed_buric",
        LeaveType.BEREAVEMENT,
        date(2026, 2, 23),
        date(2026, 2, 24),
        LeaveRequestStatus.REJECTED,
    ),
    (
        "tarxton",
        LeaveType.VACATION,
        date(2026, 1, 12),
        date(2026, 1, 16),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarxton",
        LeaveType.WFH,
        date(2026, 2, 16),
        date(2026, 2, 20),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarxton",
        LeaveType.SICK,
        date(2026, 3, 4),
        date(2026, 3, 5),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarxton",
        LeaveType.VACATION,
        date(2026, 4, 20),
        date(2026, 4, 24),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarxton",
        LeaveType.PERSONAL,
        date(2026, 5, 11),
        date(2026, 5, 12),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "tarxton",
        LeaveType.VACATION,
        date(2026, 8, 17),
        date(2026, 8, 21),
        LeaveRequestStatus.PENDING,
    ),
    (
        "rizvoni",
        LeaveType.VACATION,
        date(2026, 1, 26),
        date(2026, 1, 30),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "rizvoni",
        LeaveType.WFH,
        date(2026, 2, 23),
        date(2026, 2, 27),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "rizvoni",
        LeaveType.SICK,
        date(2026, 3, 18),
        date(2026, 3, 19),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "rizvoni",
        LeaveType.VACATION,
        date(2026, 4, 27),
        date(2026, 4, 30),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "rizvoni",
        LeaveType.PERSONAL,
        date(2026, 5, 4),
        date(2026, 5, 5),
        LeaveRequestStatus.APPROVED,
    ),
    (
        "rizvoni",
        LeaveType.WFH,
        date(2026, 6, 1),
        date(2026, 6, 5),
        LeaveRequestStatus.PENDING,
    ),
    (
        "rizvoni",
        LeaveType.MATERNITY,
        date(2026, 3, 25),
        date(2026, 3, 26),
        LeaveRequestStatus.REJECTED,
    ),
]


def seed_leave_requests():
    created = 0
    skipped = 0
    for username, leave_type, start, end, status in SEED_LEAVES:
        profile = UserProfile.objects.filter(user__username=username).first()
        if profile is None:
            continue
        exists = LeaveRequest.objects.filter(
            employee=profile,
            leave_type=leave_type,
            start_date=start,
            end_date=end,
        ).exists()
        if exists:
            skipped += 1
            continue
        LeaveRequest.objects.create(
            employee=profile,
            leave_type=leave_type,
            start_date=start,
            end_date=end,
            reason=f"Seeded {leave_type} leave for dashboard demo",
            status=status,
        )
        created += 1
    return created, skipped


def run():
    ensure_default_leave_policies()
    dept_updates = set_departments()
    created, skipped = seed_leave_requests()
    agg = materialize_leave_monthly_aggregates()
    snap = snapshot_leave_balances()
    print(f"departments_updated={dept_updates}")
    print(f"leave_requests created={created} skipped={skipped}")
    print(f"aggregates {agg}")
    print(f"snapshots {snap}")


run()
