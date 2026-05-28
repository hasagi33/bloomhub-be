"""Training budget orchestration: recalculation and threshold alerts."""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.constants import TRAINING_BUDGET_WARNING_THRESHOLD
from core.models import Notification, TrainingBudget, TrainingEntry, UserProfile
from core.services.notification_service import create_notification

logger = logging.getLogger(__name__)


def get_or_create_budget(
    employee: UserProfile,
    fiscal_year: int,
    *,
    allocated_default: Decimal = Decimal("0.00"),
) -> TrainingBudget:
    """Fetch or create a TrainingBudget row for (employee, fiscal_year)."""
    budget, _ = TrainingBudget.objects.get_or_create(
        employee=employee,
        fiscal_year=fiscal_year,
        defaults={"allocated_budget": allocated_default},
    )
    return budget


def _sum_entry_costs(employee: UserProfile, fiscal_year: int) -> Decimal:
    """Sum costs of training that has actually taken place in the fiscal year.

    Per AC ("calculated from completed courses"), we only count entries whose
    training_date is on/before today. Entries with completed_at set use that
    date as the canonical completion timestamp; otherwise training_date is the
    completion signal (it is validated to be non-future on create).
    """
    today = timezone.now().date()

    # Entries with completed_at: count when completed_at falls in the fiscal year.
    completed_total = TrainingEntry.objects.filter(
        employee=employee,
        cost__isnull=False,
        completed_at__isnull=False,
        completed_at__year=fiscal_year,
    ).aggregate(total=Sum("cost"))["total"] or Decimal("0.00")

    # Entries without completed_at: fall back to training_date, but exclude
    # any future-dated entries that may have slipped past serializer validation
    # (e.g., admin/ORM writes).
    pending_total = TrainingEntry.objects.filter(
        employee=employee,
        cost__isnull=False,
        completed_at__isnull=True,
        training_date__year=fiscal_year,
        training_date__lte=today,
    ).aggregate(total=Sum("cost"))["total"] or Decimal("0.00")

    return completed_total + pending_total


def recalculate_budget(
    employee: UserProfile, fiscal_year: int, *, actor: UserProfile | None = None
) -> TrainingBudget | None:
    """Recompute used_budget from TrainingEntry costs for the given fiscal year.

    Returns the updated budget row, or None when no budget exists and there is
    no spending to record (avoids creating empty rows on unrelated deletes).
    """
    with transaction.atomic():
        budget = (
            TrainingBudget.objects.select_for_update()
            .filter(employee=employee, fiscal_year=fiscal_year)
            .first()
        )

        total_used = _sum_entry_costs(employee, fiscal_year)

        if budget is None:
            if total_used == 0:
                return None
            budget = TrainingBudget.objects.create(
                employee=employee,
                fiscal_year=fiscal_year,
                allocated_budget=Decimal("0.00"),
                used_budget=total_used,
            )
        else:
            if budget.used_budget != total_used:
                budget.used_budget = total_used
                budget.save(update_fields=["used_budget", "updated_at"])

    _maybe_notify_threshold(budget, actor=actor)
    return budget


def _maybe_notify_threshold(
    budget: TrainingBudget, *, actor: UserProfile | None = None
) -> None:
    """Fire an 80% threshold alert at most once per crossing.

    Resets when usage drops back below the threshold so the next crossing
    re-notifies.
    """
    allocated = budget.allocated_budget or Decimal("0.00")
    used = budget.used_budget or Decimal("0.00")
    if allocated <= 0 and used <= 0:
        return

    ratio = Decimal("1.00") if allocated <= 0 else used / allocated
    above = ratio >= TRAINING_BUDGET_WARNING_THRESHOLD

    if above and budget.threshold_notified_at is None:
        _send_threshold_notifications(budget)
        budget.threshold_notified_at = timezone.now()
        budget.save(update_fields=["threshold_notified_at", "updated_at"])
    elif not above and budget.threshold_notified_at is not None:
        budget.threshold_notified_at = None
        budget.save(update_fields=["threshold_notified_at", "updated_at"])


def _send_threshold_notifications(budget: TrainingBudget) -> None:
    if budget.allocated_budget <= 0 and budget.used_budget > 0:
        percent = 100
    else:
        percent = int(round(float(budget.budget_percentage_used)))
    exceeded = budget.used_budget > budget.allocated_budget
    notif_type = Notification.Type.ALERT if exceeded else Notification.Type.WARNING

    employee_title = (
        f"Training budget {'exceeded' if exceeded else 'limit approaching'}"
    )
    employee_message = (
        f"You have used {percent}% of your {budget.fiscal_year} training budget "
        f"(${budget.used_budget:.2f} of ${budget.allocated_budget:.2f})."
    )
    metadata = {
        "training_budget_id": budget.pk,
        "fiscal_year": budget.fiscal_year,
        "percent_used": percent,
        "exceeded": exceeded,
    }

    create_notification(
        recipient=budget.employee,
        title=employee_title,
        message=employee_message,
        module=Notification.Module.TRAINING,
        type=notif_type,
        link=f"/training/budget/{budget.pk}",
        metadata=metadata,
    )

    for hr_profile in _resolve_hr_recipients():
        if hr_profile.pk == budget.employee_id:
            continue
        create_notification(
            recipient=hr_profile,
            title=f"{budget.employee.user.get_full_name()}: training budget "
            f"{'exceeded' if exceeded else 'at ' + str(percent) + '%'}",
            message=employee_message,
            module=Notification.Module.TRAINING,
            type=notif_type,
            link=f"/training/budget/{budget.pk}",
            metadata=metadata,
        )


def _resolve_hr_recipients() -> list[UserProfile]:
    """Return profiles who hold the Training budget configuration permission."""
    from core.models import Permission

    try:
        perm = Permission.objects.get(
            module_name="Training", feature_action="configure_budget"
        )
    except Permission.DoesNotExist:
        return []

    return list(
        UserProfile.objects.select_related("user")
        .filter(role__permissions=perm)
        .distinct()
    )


def get_remaining_for_year(employee: UserProfile, fiscal_year: int) -> Decimal | None:
    """Return remaining budget for the employee's fiscal year, or None if unset."""
    budget = TrainingBudget.objects.filter(
        employee=employee, fiscal_year=fiscal_year
    ).first()
    if budget is None:
        return None
    return budget.remaining_budget
