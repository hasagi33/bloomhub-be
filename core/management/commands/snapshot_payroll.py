"""Capture a monthly payroll snapshot for delta calculations.

Run on the 1st of each month. Recomputes for the supplied date (defaults to today
normalized to the first of its month). Idempotent — overwrites the row for the
same snapshot_date.
"""

from datetime import date
from decimal import Decimal
from statistics import median

from django.core.management.base import BaseCommand

from core.models import PayrollSnapshot
from core.services.compensation_service import collect_active_salaries


class Command(BaseCommand):
    help = "Compute and persist a monthly PayrollSnapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            help="ISO date (YYYY-MM-DD). Defaults to today. Normalized to first of month.",
        )

    def handle(self, *args, **options):
        today = date.today()
        if options.get("date"):
            today = date.fromisoformat(options["date"])
        snapshot_date = today.replace(day=1)

        salaries = collect_active_salaries(today)
        headcount = len(salaries)
        total = sum(salaries, Decimal("0"))
        avg = (total / Decimal(headcount)) if headcount else Decimal("0")
        med = Decimal(str(median(salaries))) if salaries else Decimal("0")

        snap, _ = PayrollSnapshot.objects.update_or_create(
            snapshot_date=snapshot_date,
            defaults={
                "total_monthly": total,
                "avg_salary": avg,
                "median_salary": med,
                "headcount": headcount,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"PayrollSnapshot {snap.snapshot_date}: total={snap.total_monthly} "
                f"avg={snap.avg_salary} median={snap.median_salary} headcount={snap.headcount}"
            )
        )
