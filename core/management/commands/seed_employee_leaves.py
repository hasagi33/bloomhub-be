"""
Seed existing employees with departments, roles, and a varied set of leave
requests spanning the current calendar year. Idempotent: re-running only fills
in missing dept/role and tops up leave entries up to the per-employee target.

Usage::

    python manage.py seed_employee_leaves
    python manage.py seed_employee_leaves --year 2026 --leaves-per-employee 5
    python manage.py seed_employee_leaves --reset
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.enums import LeaveRequestStatus, LeaveType
from core.models import (
    LeaveRequest,
    Role,
    UserProfile,
    ensure_default_leave_policies,
    initialize_leave_balances_for_profile,
)
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
    snapshot_leave_balances,
)

DEFAULT_DEPARTMENTS = [
    "Engineering",
    "Product",
    "Design",
    "Sales",
    "Marketing",
    "People & Culture",
    "Finance",
    "Customer Success",
    "Operations",
]

DEFAULT_ROLES = [
    ("Software Engineer", "Builds and maintains product features."),
    ("Senior Software Engineer", "Leads delivery on cross-cutting initiatives."),
    ("Product Manager", "Owns product discovery and roadmap."),
    ("Product Designer", "Designs product surfaces and flows."),
    ("Engineering Manager", "Manages a delivery team and engineering practice."),
    ("Account Executive", "Closes new-business deals."),
    ("Marketing Specialist", "Runs campaigns and content."),
    ("People Partner", "Supports employees on HR matters."),
    ("Financial Analyst", "Owns budgeting and reporting."),
    ("Customer Success Manager", "Owns post-sale customer health."),
]

LEAVE_TYPE_WEIGHTS = [
    (LeaveType.VACATION, 5),
    (LeaveType.SICK, 3),
    (LeaveType.WFH, 3),
    (LeaveType.PERSONAL, 2),
    (LeaveType.BEREAVEMENT, 1),
    (LeaveType.UNPAID, 1),
    (LeaveType.MATERNITY, 1),
    (LeaveType.PATERNITY, 1),
]

STATUS_WEIGHTS = [
    (LeaveRequestStatus.APPROVED, 7),
    (LeaveRequestStatus.PENDING, 2),
    (LeaveRequestStatus.REJECTED, 1),
]

REASON_BY_TYPE = {
    LeaveType.VACATION: "Annual leave",
    LeaveType.SICK: "Sick leave",
    LeaveType.WFH: "Remote work",
    LeaveType.PERSONAL: "Personal day",
    LeaveType.BEREAVEMENT: "Family bereavement",
    LeaveType.UNPAID: "Unpaid leave",
    LeaveType.MATERNITY: "Maternity leave",
    LeaveType.PATERNITY: "Paternity leave",
}

SEED_REASON_PREFIX = "[seed]"


class Command(BaseCommand):
    help = (
        "Seed existing employees with departments, roles, and varied leave "
        "requests. Idempotent — safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=None,
            help="Target year for leave requests (defaults to current year).",
        )
        parser.add_argument(
            "--leaves-per-employee",
            type=int,
            default=6,
            help="Target leave-request count per employee (default 6).",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="Random seed for reproducible runs (default 42).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Delete previously seeded LeaveRequest rows "
                "(marked with the [seed] reason prefix) before seeding."
            ),
        )
        parser.add_argument(
            "--skip-aggregates",
            action="store_true",
            help="Skip the leave_analytics materialize/snapshot step.",
        )

    def handle(self, *args, **opts):
        year = opts["year"] or timezone.now().year
        target_per_employee = max(1, int(opts["leaves_per_employee"]))
        rng = random.Random(opts["seed"])
        reset = bool(opts["reset"])
        skip_aggregates = bool(opts["skip_aggregates"])

        self.stdout.write(self.style.NOTICE("Ensuring default leave policies…"))
        ensure_default_leave_policies()

        roles = self._ensure_roles()
        departments = self._department_pool()

        employees = list(UserProfile.objects.select_related("user").order_by("id"))
        if not employees:
            self.stdout.write(self.style.WARNING("No employees found."))
            return

        with transaction.atomic():
            if reset:
                deleted, _ = LeaveRequest.objects.filter(
                    reason__startswith=SEED_REASON_PREFIX,
                ).delete()
                self.stdout.write(f"Reset: deleted {deleted} seeded leave rows.")

            # Drop pointers to orphan dbg-* roles so seeding can reassign.
            dbg_role_ids = set(
                Role.objects.filter(name__istartswith="dbg-").values_list(
                    "id", flat=True
                )
            )
            cleared_dbg = 0
            if dbg_role_ids:
                cleared_dbg = UserProfile.objects.filter(
                    role_id__in=dbg_role_ids
                ).update(role=None)
                Role.objects.filter(id__in=dbg_role_ids).delete()
            if cleared_dbg:
                self.stdout.write(
                    f"Cleared {cleared_dbg} dbg-* role assignments and removed "
                    f"{len(dbg_role_ids)} debug role(s)."
                )
                employees = list(
                    UserProfile.objects.select_related("user").order_by("id")
                )

            updated_roles = 0
            updated_depts = 0
            for index, employee in enumerate(employees):
                if employee.role_id is None and roles:
                    employee.role = roles[index % len(roles)]
                    updated_roles += 1
                if not employee.department and departments:
                    employee.department = departments[index % len(departments)]
                    updated_depts += 1
                employee.save(update_fields=["role", "department"])

            self.stdout.write(
                f"Roles assigned: {updated_roles}. "
                f"Departments assigned: {updated_depts}."
            )

            total_balances = 0
            total_created = 0
            total_skipped = 0
            for employee in employees:
                total_balances += initialize_leave_balances_for_profile(employee)
                created, skipped = self._seed_employee_leaves(
                    employee=employee,
                    year=year,
                    target=target_per_employee,
                    rng=rng,
                )
                total_created += created
                total_skipped += skipped

        self.stdout.write(
            f"Leave balances created: {total_balances}. "
            f"Leave requests created: {total_created}, skipped: {total_skipped}."
        )

        if skip_aggregates:
            return

        self.stdout.write("Materializing leave_analytics aggregates…")
        agg = materialize_leave_monthly_aggregates(year_range=(year, year))
        snap = snapshot_leave_balances()
        self.stdout.write(self.style.SUCCESS(f"Aggregates: {agg}"))
        self.stdout.write(self.style.SUCCESS(f"Snapshots:  {snap}"))

    # ── helpers ────────────────────────────────────────────────────────

    def _ensure_roles(self) -> list[Role]:
        # Skip orphan debug roles (e.g. `dbg-role-2`) left from prior testing.
        real_existing = list(
            Role.objects.exclude(name__istartswith="dbg-").order_by("id")
        )
        if real_existing:
            return real_existing
        created: list[Role] = []
        for name, description in DEFAULT_ROLES:
            role, _ = Role.objects.get_or_create(
                name=name,
                defaults={"description": description},
            )
            created.append(role)
        return created

    def _department_pool(self) -> list[str]:
        existing = list(
            UserProfile.objects.exclude(department__isnull=True)
            .exclude(department__exact="")
            .values_list("department", flat=True)
            .distinct()
        )
        existing = sorted({d for d in existing if d})
        if existing:
            extras = [d for d in DEFAULT_DEPARTMENTS if d not in existing]
            return existing + extras
        return list(DEFAULT_DEPARTMENTS)

    def _seed_employee_leaves(
        self,
        *,
        employee: UserProfile,
        year: int,
        target: int,
        rng: random.Random,
    ) -> tuple[int, int]:
        existing_count = LeaveRequest.objects.filter(
            employee=employee,
            start_date__year=year,
        ).count()
        needed = max(0, target - existing_count)
        if needed == 0:
            return 0, target

        # Per-employee deterministic offset so runs are stable.
        employee_rng = random.Random(rng.random() + employee.id)
        created = 0
        skipped = 0
        attempts = 0
        max_attempts = needed * 6
        while created < needed and attempts < max_attempts:
            attempts += 1
            leave_type = _weighted_choice(LEAVE_TYPE_WEIGHTS, employee_rng)
            status = _weighted_choice(STATUS_WEIGHTS, employee_rng)
            start, end = _random_working_span(year, employee_rng)

            overlap = LeaveRequest.objects.filter(
                employee=employee,
                start_date__lte=end,
                end_date__gte=start,
            ).exists()
            if overlap:
                skipped += 1
                continue

            LeaveRequest.objects.create(
                employee=employee,
                leave_type=leave_type,
                start_date=start,
                end_date=end,
                reason=f"{SEED_REASON_PREFIX} {REASON_BY_TYPE[leave_type]}",
                status=status,
            )
            created += 1
        return created, skipped


def _weighted_choice(weighted: list[tuple], rng: random.Random):
    total = sum(weight for _, weight in weighted)
    pick = rng.uniform(0, total)
    upto = 0.0
    for value, weight in weighted:
        upto += weight
        if pick <= upto:
            return value
    return weighted[-1][0]


def _random_working_span(year: int, rng: random.Random) -> tuple[date, date]:
    """Pick a Mon–Fri-anchored span between 1 and 10 working days long."""
    day_of_year = rng.randint(1, 360)
    start = date(year, 1, 1) + timedelta(days=day_of_year - 1)
    # Shift to nearest Monday (mostly Mon-start to avoid noisy split aggregates).
    while start.weekday() >= 5:
        start += timedelta(days=1)
    length = rng.randint(1, 10)
    end = start + timedelta(days=length - 1)
    # Clamp inside the same year for clean aggregate buckets.
    last_day = date(year, 12, 31)
    if end > last_day:
        end = last_day
    return start, end
