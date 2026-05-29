"""
Leave Analytics Service

Materializes pre-aggregated leave statistics from `LeaveRequest` and
`LeaveBalance` into the analytics fact tables (`LeaveMonthlyAggregate`,
`LeaveBalanceSnapshot`). Read paths can then query the fact tables directly
instead of recomputing aggregations on every API call.

Public entry points:

    materialize_leave_monthly_aggregates(*, employee=None, year_range=None,
                                        reference_time=None)
        Rebuild monthly aggregates for one employee or for everyone.

    snapshot_leave_balances(*, employees=None, year=None, snapshot_date=None)
        Take a snapshot of current leave balances for trend reporting.

Both return a summary dict so callers (management commands, tests) can log how
much work was done.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.constants import LEAVE_ANALYTICS_STATUS_BUCKET
from core.enums import LeaveType
from core.models import (
    LeaveBalance,
    LeaveBalanceSnapshot,
    LeaveMonthlyAggregate,
    LeaveRequest,
    UserProfile,
)


@dataclass(frozen=True)
class _BucketKey:
    employee_id: int
    leave_type: str
    year: int
    month: int


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _iter_working_days(start: date, end: date) -> Iterable[date]:
    """Yield each Mon–Fri date between start and end (inclusive)."""
    current = start
    while current <= end:
        if not _is_weekend(current):
            yield current
        current += timedelta(days=1)


def _distribute_request_days(
    leave_request: LeaveRequest,
) -> dict[tuple[int, int], int]:
    """
    Working days from `leave_request`, bucketed by (year, month).

    Returns
    -------
    dict mapping (year, month) -> working_day_count
    """
    buckets: dict[tuple[int, int], int] = defaultdict(int)
    for day in _iter_working_days(leave_request.start_date, leave_request.end_date):
        buckets[(day.year, day.month)] += 1
    return buckets


def _year_filter(year_range: tuple[int, int] | None):
    """Build a Django ORM filter narrowing to overlap with year_range."""
    if year_range is None:
        return {}
    start_year, end_year = year_range
    return {
        "start_date__lte": date(end_year, 12, 31),
        "end_date__gte": date(start_year, 1, 1),
    }


def materialize_leave_monthly_aggregates(
    *,
    employee: UserProfile | None = None,
    year_range: tuple[int, int] | None = None,
    reference_time: datetime | None = None,
) -> dict[str, int]:
    """
    Rebuild `LeaveMonthlyAggregate` rows from `LeaveRequest` source data.

    Parameters
    ----------
    employee:
        Limit the rebuild to a single employee. ``None`` rebuilds for everyone.
    year_range:
        Two-tuple ``(start_year, end_year)`` inclusive. Buckets falling fully
        outside the range are skipped on read AND deleted on rebuild to keep
        the fact table tidy. ``None`` means "all years touched by requests".
    reference_time:
        Reserved for future use (e.g. computing pending-as-of). Currently
        unused; accepted to keep the service signature stable.

    Returns
    -------
    Summary dict with `created_count`, `updated_count`, `deleted_count`.
    """
    _ = reference_time  # reserved

    requests_qs = LeaveRequest.objects.all().only(
        "id", "employee_id", "leave_type", "start_date", "end_date", "status"
    )
    if employee is not None:
        requests_qs = requests_qs.filter(employee=employee)
    requests_qs = requests_qs.filter(**_year_filter(year_range))

    # Build the desired state in memory: bucket -> {status_field: days, requests_count}
    desired: dict[_BucketKey, dict[str, int]] = defaultdict(
        lambda: {field: 0 for field in LEAVE_ANALYTICS_STATUS_BUCKET.values()}
        | {"requests_count": 0}
    )
    for lr in requests_qs:
        if lr.status not in LEAVE_ANALYTICS_STATUS_BUCKET:
            continue
        bucket_field = LEAVE_ANALYTICS_STATUS_BUCKET[lr.status]
        per_month = _distribute_request_days(lr)
        for (year, month), days in per_month.items():
            if year_range and not (year_range[0] <= year <= year_range[1]):
                continue
            key = _BucketKey(
                employee_id=lr.employee_id,
                leave_type=lr.leave_type,
                year=year,
                month=month,
            )
            desired[key][bucket_field] += days
            desired[key]["requests_count"] += 1

    created_count = 0
    updated_count = 0
    deleted_count = 0

    with transaction.atomic():
        # Compute the scope we're allowed to touch so we can prune stale rows.
        scope_qs = LeaveMonthlyAggregate.objects.all()
        if employee is not None:
            scope_qs = scope_qs.filter(employee=employee)
        if year_range is not None:
            scope_qs = scope_qs.filter(year__gte=year_range[0], year__lte=year_range[1])

        existing_by_key: dict[_BucketKey, LeaveMonthlyAggregate] = {
            _BucketKey(
                employee_id=row.employee_id,
                leave_type=row.leave_type,
                year=row.year,
                month=row.month,
            ): row
            for row in scope_qs
        }

        to_create: list[LeaveMonthlyAggregate] = []
        for key, values in desired.items():
            row = existing_by_key.pop(key, None)
            if row is None:
                to_create.append(
                    LeaveMonthlyAggregate(
                        employee_id=key.employee_id,
                        leave_type=key.leave_type,
                        year=key.year,
                        month=key.month,
                        **values,
                    )
                )
            else:
                changed = False
                for field, value in values.items():
                    if getattr(row, field) != value:
                        setattr(row, field, value)
                        changed = True
                if changed:
                    row.save(update_fields=[*values.keys(), "updated_at"])
                    updated_count += 1

        if to_create:
            LeaveMonthlyAggregate.objects.bulk_create(to_create)
            created_count = len(to_create)

        # Any rows left in `existing_by_key` are stale (request was deleted or
        # moved out of the bucket). Drop them so reports don't show ghosts.
        if existing_by_key:
            stale_ids = [row.id for row in existing_by_key.values()]
            deleted_count, _ = LeaveMonthlyAggregate.objects.filter(
                id__in=stale_ids
            ).delete()

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "deleted_count": deleted_count,
    }


def snapshot_leave_balances(
    *,
    employees: Iterable[UserProfile] | None = None,
    year: int | None = None,
    snapshot_date: date | None = None,
) -> dict[str, int]:
    """
    Capture a `LeaveBalanceSnapshot` row for each current `LeaveBalance`.

    Idempotent per ``(employee, leave_type, year, snapshot_date)`` — calling
    twice on the same day updates the existing row rather than duplicating.
    """
    if snapshot_date is None:
        snapshot_date = timezone.now().date()
    if year is None:
        year = snapshot_date.year

    balances_qs = LeaveBalance.objects.filter(year=year).select_related("employee")
    if employees is not None:
        balances_qs = balances_qs.filter(employee__in=list(employees))

    created_count = 0
    updated_count = 0

    with transaction.atomic():
        for balance in balances_qs:
            remaining = max(0, (balance.allocated + balance.carryover) - balance.used)
            snapshot, created = LeaveBalanceSnapshot.objects.update_or_create(
                employee=balance.employee,
                leave_type=balance.leave_type,
                year=balance.year,
                snapshot_date=snapshot_date,
                defaults={
                    "allocated": balance.allocated,
                    "used": balance.used,
                    "carryover": balance.carryover,
                    "remaining": remaining,
                },
            )
            _ = snapshot
            if created:
                created_count += 1
            else:
                updated_count += 1

    return {
        "created_count": created_count,
        "updated_count": updated_count,
    }


# ──────────────────────────────────────────
# Read helpers — thin wrappers reused by views/serializers/management cmds.
# Keep these query-only: aggregation lives in the DB, not in Python.
# ──────────────────────────────────────────


def monthly_breakdown(
    *,
    year: int,
    leave_type: str | None = None,
    employee: UserProfile | None = None,
) -> list[LeaveMonthlyAggregate]:
    """Return all monthly buckets for a year, optionally narrowed."""
    qs = LeaveMonthlyAggregate.objects.filter(year=year)
    if leave_type is not None:
        qs = qs.filter(leave_type=leave_type)
    if employee is not None:
        qs = qs.filter(employee=employee)
    return list(qs.order_by("month", "leave_type"))


def yearly_totals_by_type(year: int) -> dict[str, int]:
    """Return ``{leave_type: approved_days}`` for ``year``."""
    from django.db.models import Sum

    rows = (
        LeaveMonthlyAggregate.objects.filter(year=year)
        .values("leave_type")
        .annotate(total=Sum("approved_days"))
    )
    totals: dict[str, int] = {lt: 0 for lt in LeaveType.values}
    for row in rows:
        totals[row["leave_type"]] = row["total"] or 0
    return totals
