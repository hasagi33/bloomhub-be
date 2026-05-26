"""Compensation aggregation helpers.

Centralises the math used by:
  - GET /api/compensation/overview/
  - EmployeeProfileSerializer current_net_salary / current_total_monthly / bonus_pct / status
  - the snapshot_payroll management command

Pay model is policy-driven:
  net_monthly  ← CompensationPolicy.objects.get(cpf_level=profile.cpf_level)
  benefits     ← Σ BenefitCatalog.objects.filter(active)
  total        = net + Σ benefits
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Q

from core.enums import (
    BonusType,
    CompensationStatus,
    EmploymentStatus,
    LeaveRequestStatus,
)

SALARY_BANDS = [
    ("BAM 1.5k–2k", Decimal("1500"), Decimal("2000")),
    ("BAM 2k–2.5k", Decimal("2000"), Decimal("2500")),
    ("BAM 2.5k–3.5k", Decimal("2500"), Decimal("3500")),
    ("BAM 3.5k–4.5k", Decimal("3500"), Decimal("4500")),
    ("BAM 4.5k–6k", Decimal("4500"), Decimal("6000")),
    ("BAM 6k +", Decimal("6000"), None),
]


# ── Policy + catalog resolution ──────────────────────────────────────────


def resolve_policy(profile):
    """Resolve active policy for an employee by CPF level."""
    from core.models import CompensationPolicy

    if not getattr(profile, "cpf_level", None):
        return None
    return CompensationPolicy.objects.filter(cpf_level=profile.cpf_level).first()


def active_benefits(today: date | None = None):
    """Active catalog entries for the given date."""
    from core.models import BenefitCatalog

    today = today or date.today()
    qs = BenefitCatalog.objects.filter(is_active=True, effective_date__lte=today)
    return qs.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))


def total_benefits_monthly(today: date | None = None) -> Decimal:
    today = today or date.today()
    total = Decimal("0")
    for b in active_benefits(today):
        total += b.monthly_amount
    return total


def resolve_employee_net_salary(profile) -> Decimal | None:
    """Resolve employee NET salary, falling back to saved salary records.

    CPF policies are the source of truth when configured. Some tests and legacy
    employees still carry salary through SalaryRecord, so keep that value visible
    until a policy exists for their CPF level.
    """
    policy = resolve_policy(profile)
    if policy:
        return Decimal(policy.net_monthly)
    amount = profile.current_salary
    return Decimal(amount) if amount is not None else None


def resolve_employee_total(profile, today: date | None = None) -> dict:
    """Resolve { net, benefits, total } for one employee via policy + catalog."""
    policy = resolve_policy(profile)
    benefits = total_benefits_monthly(today)
    net = resolve_employee_net_salary(profile) or Decimal("0")
    return {
        "net_monthly": net,
        "benefits_monthly": benefits,
        "total_monthly": net + benefits,
        "cpf_level": profile.cpf_level or None,
        "policy_id": policy.id if policy else None,
    }


# ── Compensation status (Active / OnLeave / PTO) ─────────────────────────


def _on_pto(profile, today: date | None = None) -> bool:
    today = today or date.today()
    try:
        return profile.leave_requests.filter(
            status=LeaveRequestStatus.APPROVED,
            start_date__lte=today,
            end_date__gte=today,
        ).exists()
    except Exception:
        return False


def compute_compensation_status(profile) -> str:
    if profile.employment_status == EmploymentStatus.INACTIVE:
        return CompensationStatus.ON_LEAVE
    if profile.employment_status == EmploymentStatus.ON_LEAVE:
        return CompensationStatus.ON_LEAVE
    if _on_pto(profile):
        return CompensationStatus.PTO
    return CompensationStatus.ACTIVE


# ── Bonus % ──────────────────────────────────────────────────────────────


def compute_bonus_pct(profile, today: date | None = None) -> float:
    """bonus % = sum(bonus.amount last 12mo) / 12 / current_salary * 100."""
    today = today or date.today()
    current_salary = profile.current_salary
    if not current_salary or current_salary <= 0:
        return 0.0
    since = today - timedelta(days=365)
    total = Decimal("0")
    for b in profile.bonus_records.filter(effective_date__gte=since):
        total += b.amount
    if total == 0:
        return 0.0
    monthly_avg = total / Decimal("12")
    return float((monthly_avg / Decimal(current_salary)) * Decimal("100"))


# ── Aggregation helpers ──────────────────────────────────────────────────


def _active_profiles_qs():
    from core.models import UserProfile

    return UserProfile.objects.filter(
        employment_status=EmploymentStatus.ACTIVE
    ).prefetch_related("salary_records")


def collect_active_salaries(today: date | None = None) -> list[Decimal]:
    """current_salary (gross) values for active employees, excluding nulls."""
    salaries: list[Decimal] = []
    for profile in _active_profiles_qs():
        amount = profile.current_salary
        if amount is None:
            continue
        salaries.append(Decimal(amount))
    return salaries


def collect_active_net_salaries(today: date | None = None) -> list[Decimal]:
    """Policy-resolved NET values for active employees, with salary fallback."""
    nets: list[Decimal] = []
    for profile in _active_profiles_qs():
        amount = resolve_employee_net_salary(profile)
        if amount is None:
            continue
        nets.append(Decimal(amount))
    return nets


def total_monthly_net(today: date | None = None) -> Decimal:
    """Sum of policy NET across active employees that have a policy."""
    return sum(collect_active_net_salaries(today), Decimal("0"))


# ── Bands + mix ──────────────────────────────────────────────────────────


def build_bands(salaries: Iterable[Decimal]) -> list[dict]:
    salaries = list(salaries)
    total = len(salaries)
    bands = []
    for label, lo, hi in SALARY_BANDS:
        count = sum(1 for s in salaries if s >= lo and (hi is None or s < hi))
        pct = float((count / total) * 100) if total else 0.0
        bands.append({"label": label, "count": count, "pct": pct})
    return bands


def build_mix(
    net_total: Decimal, benefits_total: Decimal, today: date | None = None
) -> list[dict]:
    from core.models import BonusRecord

    today = today or date.today()
    since = today - timedelta(days=365)
    bonuses = BonusRecord.objects.filter(effective_date__gte=since)

    perf_total = sum(
        (b.amount for b in bonuses if b.bonus_type == BonusType.PERFORMANCE),
        Decimal("0"),
    )
    proj_edu_total = sum(
        (
            b.amount
            for b in bonuses
            if b.bonus_type in (BonusType.PROJECT, BonusType.EDUCATION)
        ),
        Decimal("0"),
    )
    perf_monthly = perf_total / Decimal("12") if perf_total else Decimal("0")
    proj_edu_monthly = (
        proj_edu_total / Decimal("12") if proj_edu_total else Decimal("0")
    )

    grand_total = net_total + perf_monthly + proj_edu_monthly + benefits_total
    if grand_total <= 0:
        return [
            {"name": "Base salary", "pct": 0.0, "color": "indigo"},
            {"name": "Performance bonus", "pct": 0.0, "color": "emerald"},
            {"name": "Project & education bonus", "pct": 0.0, "color": "violet"},
            {"name": "Benefits", "pct": 0.0, "color": "amber"},
        ]

    def pct(part: Decimal) -> float:
        return float((part / grand_total) * Decimal("100"))

    return [
        {"name": "Base salary", "pct": pct(net_total), "color": "indigo"},
        {"name": "Performance bonus", "pct": pct(perf_monthly), "color": "emerald"},
        {
            "name": "Project & education bonus",
            "pct": pct(proj_edu_monthly),
            "color": "violet",
        },
        {"name": "Benefits", "pct": pct(benefits_total), "color": "amber"},
    ]


# ── Stats + overview ─────────────────────────────────────────────────────


def compute_stats(today: date | None = None) -> dict:
    from core.models import PerformanceReview

    today = today or date.today()
    # Policy-driven compensation uses CPF NET policies as the source of truth.
    # Old gross SalaryRecord rows may not exist for seeded employees.
    salaries = collect_active_net_salaries(today)
    total_employees = len(salaries)
    total_net_salary = sum(salaries, Decimal("0"))
    avg_salary = (
        (total_net_salary / Decimal(total_employees))
        if total_employees
        else Decimal("0")
    )
    med_salary = Decimal(str(median(salaries))) if salaries else Decimal("0")

    # totalMonthly per new spec: Σ policy_net + headcount * benefits_per_employee
    net_total = total_net_salary
    benefits_per_employee = total_benefits_monthly(today)
    active_headcount = _active_profiles_qs().count()
    total_monthly = net_total + Decimal(active_headcount) * benefits_per_employee

    pending_qs = PerformanceReview.objects.filter(status="scheduled")
    pending = pending_qs.count()
    overdue = pending_qs.filter(scheduled_date__lt=today).count()

    monthly_delta = _pct_delta(
        total_monthly, _snapshot_field(today, months=1, field="total_monthly")
    )
    yoy_delta = _pct_delta(
        avg_salary, _snapshot_field(today, months=12, field="avg_salary")
    )
    qoq_delta = _pct_delta(
        med_salary, _snapshot_field(today, months=3, field="median_salary")
    )

    return {
        "totalMonthly": float(total_monthly),
        "totalMonthlyNet": float(net_total),
        "totalMonthlyBenefits": float(
            benefits_per_employee * Decimal(active_headcount)
        ),
        "benefitsPerEmployee": float(benefits_per_employee),
        "avgSalary": float(avg_salary),
        "medianSalary": float(med_salary),
        "pendingReviews": pending,
        "overdueReviews": overdue,
        "totalEmployees": total_employees,
        "monthlyDeltaPct": monthly_delta,
        "avgYoyPct": yoy_delta,
        "medianQoqPct": qoq_delta,
        "_net_total_decimal": net_total,
        "_benefits_total_decimal": benefits_per_employee * Decimal(active_headcount),
    }


def _snapshot_field(today: date, months: int, field: str) -> Decimal | None:
    from core.models import PayrollSnapshot

    target_year = today.year
    target_month = today.month - months
    while target_month <= 0:
        target_month += 12
        target_year -= 1
    target = date(target_year, target_month, 1)
    snap = PayrollSnapshot.objects.filter(snapshot_date=target).first()
    if not snap:
        return None
    return getattr(snap, field)


def _pct_delta(current: Decimal, previous: Decimal | None) -> float:
    if previous is None or previous == 0:
        return 0.0
    return float(
        ((Decimal(current) - Decimal(previous)) / Decimal(previous)) * Decimal("100")
    )


def build_overview(today: date | None = None) -> dict:
    from core.models import UserProfile

    today = today or date.today()
    stats = compute_stats(today)
    net_total = stats.pop("_net_total_decimal")
    benefits_total = stats.pop("_benefits_total_decimal")
    salaries = collect_active_net_salaries(today)
    bands = build_bands(salaries)
    mix = build_mix(net_total, benefits_total, today)

    employees = []
    qs = UserProfile.objects.select_related("user", "role").prefetch_related(
        "salary_records", "bonus_records", "leave_requests"
    )
    benefits_per_employee = total_benefits_monthly(today)
    for profile in qs:
        employees.append(_employee_row(profile, today, benefits_per_employee))

    return {
        "stats": stats,
        "bands": bands,
        "mix": mix,
        "employees": employees,
    }


_COLOR_PALETTE = ("green", "indigo", "rose", "gray", "orange")


def _hash_color(name: str) -> str:
    if not name:
        return _COLOR_PALETTE[0]
    return _COLOR_PALETTE[sum(ord(c) for c in name) % len(_COLOR_PALETTE)]


def _employee_row(profile, today: date, benefits_per_employee: Decimal) -> dict:
    last_review = (
        profile.performance_reviews.filter(status="completed")
        .order_by("-scheduled_date")
        .first()
    )
    next_review = (
        profile.performance_reviews.filter(status="scheduled")
        .order_by("scheduled_date")
        .first()
    )
    name = profile.full_name or profile.user.get_full_name() or profile.user.username
    policy = resolve_policy(profile)
    net = resolve_employee_net_salary(profile) or Decimal("0")
    total = Decimal(net) + benefits_per_employee
    return {
        "id": profile.id,
        "name": name,
        "title": profile.role.name if profile.role else "",
        "dept": profile.department or "",
        "salary": float(net),
        "cpf_level": profile.cpf_level or None,
        "policy_id": policy.id if policy else None,
        "benefits_monthly": float(benefits_per_employee),
        "total_monthly": float(total),
        "bonus": compute_bonus_pct(profile, today),
        "last": last_review.scheduled_date.isoformat() if last_review else "",
        "next": next_review.scheduled_date.isoformat() if next_review else "",
        "status": compute_compensation_status(profile),
        "color": _hash_color(name),
    }
