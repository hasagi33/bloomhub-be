from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import UserProfile
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
    snapshot_leave_balances,
)


class Command(BaseCommand):
    help = (
        "Rebuild LeaveMonthlyAggregate rows from LeaveRequest data and "
        "(optionally) take a snapshot of current LeaveBalance values."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--employee-id",
            type=int,
            default=None,
            help="Restrict rebuild to a single UserProfile id.",
        )
        parser.add_argument(
            "--year-from",
            type=int,
            default=None,
            help="Start year (inclusive) of the rebuild window.",
        )
        parser.add_argument(
            "--year-to",
            type=int,
            default=None,
            help="End year (inclusive) of the rebuild window.",
        )
        parser.add_argument(
            "--skip-snapshot",
            action="store_true",
            help="Skip taking a LeaveBalanceSnapshot for the current year.",
        )
        parser.add_argument(
            "--snapshot-date",
            type=str,
            default=None,
            help="Snapshot date in YYYY-MM-DD (defaults to today UTC).",
        )

    def handle(self, *args, **options):
        employee = None
        if options["employee_id"] is not None:
            try:
                employee = UserProfile.objects.get(id=options["employee_id"])
            except UserProfile.DoesNotExist as exc:
                raise CommandError(
                    f"No UserProfile with id={options['employee_id']}"
                ) from exc

        year_from = options["year_from"]
        year_to = options["year_to"]
        year_range = None
        if year_from is not None or year_to is not None:
            if year_from is None or year_to is None:
                raise CommandError(
                    "Pass --year-from and --year-to together, or neither."
                )
            if year_from > year_to:
                raise CommandError("--year-from cannot exceed --year-to.")
            year_range = (year_from, year_to)

        agg_stats = materialize_leave_monthly_aggregates(
            employee=employee,
            year_range=year_range,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Aggregates: created={created_count}, "
                "updated={updated_count}, deleted={deleted_count}".format(**agg_stats)
            )
        )

        if options["skip_snapshot"]:
            return

        snapshot_date: date | None = None
        if options["snapshot_date"]:
            try:
                snapshot_date = date.fromisoformat(options["snapshot_date"])
            except ValueError as exc:
                raise CommandError(
                    "--snapshot-date must be YYYY-MM-DD"
                ) from exc
        snap_year = (snapshot_date or timezone.now().date()).year

        snap_stats = snapshot_leave_balances(
            employees=[employee] if employee is not None else None,
            year=snap_year,
            snapshot_date=snapshot_date,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Snapshots: created={created_count}, "
                "updated={updated_count}".format(**snap_stats)
            )
        )
