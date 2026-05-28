from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework.test import APITestCase

from core.enums import LeaveRequestStatus, LeaveType
from core.models import (
    LeaveBalance,
    LeaveBalanceSnapshot,
    LeaveMonthlyAggregate,
    LeavePolicy,
    LeaveRequest,
)
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
    monthly_breakdown,
    snapshot_leave_balances,
    yearly_totals_by_type,
)


class LeaveAnalyticsServiceTestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command(
            "setup_public_tenant", "--domain", "testserver", verbosity=0
        )

    @staticmethod
    def _create_profile(username: str):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="pass123",
            first_name=username,
            last_name="User",
        )
        return user.profile

    @staticmethod
    def _ensure_policy(leave_type=LeaveType.VACATION, allocated=25, carryover=5):
        LeavePolicy.objects.update_or_create(
            leave_type=leave_type,
            defaults={
                "allocated_days_per_year": allocated,
                "carryover_days": carryover,
                "requires_approval": True,
                "requires_covering_employee": False,
                "min_notice_in_days": 0,
                "max_consecutive_days": None,
            },
        )

    @staticmethod
    def _ensure_balance(
        profile,
        *,
        leave_type=LeaveType.VACATION,
        year=2026,
        allocated=25,
        used=0,
        carryover=2,
    ):
        balance, _ = LeaveBalance.objects.update_or_create(
            employee=profile,
            leave_type=leave_type,
            year=year,
            defaults={
                "allocated": allocated,
                "used": used,
                "carryover": carryover,
            },
        )
        return balance

    @staticmethod
    def _make_request(
        profile,
        *,
        leave_type=LeaveType.VACATION,
        start=date(2026, 3, 2),  # Monday
        end=date(2026, 3, 6),    # Friday
        status=LeaveRequestStatus.APPROVED,
    ):
        return LeaveRequest.objects.create(
            employee=profile,
            leave_type=leave_type,
            start_date=start,
            end_date=end,
            reason="test",
            status=status,
        )

    def test_materialize_monthly_aggregates_creates_buckets_for_approved_request(
        self,
    ):
        profile = self._create_profile("emp-monthly-1")
        self._make_request(profile)  # Mar 2-6 = 5 working days, approved

        stats = materialize_leave_monthly_aggregates()
        self.assertEqual(stats["created_count"], 1)
        self.assertEqual(stats["updated_count"], 0)
        self.assertEqual(stats["deleted_count"], 0)

        rows = list(LeaveMonthlyAggregate.objects.all())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.employee_id, profile.id)
        self.assertEqual(row.leave_type, LeaveType.VACATION)
        self.assertEqual(row.year, 2026)
        self.assertEqual(row.month, 3)
        self.assertEqual(row.approved_days, 5)
        self.assertEqual(row.pending_days, 0)
        self.assertEqual(row.requests_count, 1)

    def test_materialize_splits_cross_month_request_into_two_rows(self):
        profile = self._create_profile("emp-monthly-2")
        # Mon Mar 30 → Fri Apr 3 2026 (5 working days: Mar 30/31 + Apr 1/2/3)
        self._make_request(
            profile, start=date(2026, 3, 30), end=date(2026, 4, 3)
        )

        materialize_leave_monthly_aggregates()

        march = LeaveMonthlyAggregate.objects.get(
            year=2026, month=3, employee=profile
        )
        april = LeaveMonthlyAggregate.objects.get(
            year=2026, month=4, employee=profile
        )
        self.assertEqual(march.approved_days, 2)
        self.assertEqual(april.approved_days, 3)
        # Each bucket records that one source request contributed to it.
        self.assertEqual(march.requests_count, 1)
        self.assertEqual(april.requests_count, 1)

    def test_status_routes_into_correct_bucket(self):
        profile = self._create_profile("emp-monthly-3")
        self._make_request(
            profile,
            start=date(2026, 5, 4),
            end=date(2026, 5, 6),
            status=LeaveRequestStatus.PENDING,
        )
        self._make_request(
            profile,
            start=date(2026, 5, 11),
            end=date(2026, 5, 12),
            status=LeaveRequestStatus.REJECTED,
        )
        self._make_request(
            profile,
            start=date(2026, 5, 18),
            end=date(2026, 5, 18),
            status=LeaveRequestStatus.CANCELLED,
        )

        materialize_leave_monthly_aggregates()

        row = LeaveMonthlyAggregate.objects.get(
            employee=profile, year=2026, month=5
        )
        self.assertEqual(row.approved_days, 0)
        self.assertEqual(row.pending_days, 3)
        self.assertEqual(row.rejected_days, 2)
        self.assertEqual(row.cancelled_days, 1)
        self.assertEqual(row.requests_count, 3)

    def test_rebuild_is_idempotent_and_prunes_stale(self):
        profile = self._create_profile("emp-monthly-4")
        leave_request = self._make_request(profile)

        materialize_leave_monthly_aggregates()
        second = materialize_leave_monthly_aggregates()
        self.assertEqual(
            second,
            {"created_count": 0, "updated_count": 0, "deleted_count": 0},
        )

        # Delete the request → next rebuild prunes the now-orphaned bucket.
        leave_request.delete()
        third = materialize_leave_monthly_aggregates()
        self.assertEqual(third["deleted_count"], 1)
        self.assertEqual(LeaveMonthlyAggregate.objects.count(), 0)

    def test_year_range_scope_isolates_other_years(self):
        profile = self._create_profile("emp-monthly-5")
        self._make_request(
            profile, start=date(2024, 2, 5), end=date(2024, 2, 7)
        )
        self._make_request(
            profile, start=date(2026, 2, 3), end=date(2026, 2, 5)
        )

        materialize_leave_monthly_aggregates(year_range=(2026, 2026))

        self.assertTrue(
            LeaveMonthlyAggregate.objects.filter(year=2026).exists()
        )
        self.assertFalse(
            LeaveMonthlyAggregate.objects.filter(year=2024).exists()
        )

    def test_snapshot_leave_balances_writes_and_updates(self):
        profile = self._create_profile("emp-snapshot")
        self._ensure_policy()
        self._ensure_balance(profile, allocated=25, used=4, carryover=2)

        snap_date = date(2026, 4, 30)
        expected_balance_count = LeaveBalance.objects.filter(
            employee=profile, year=2026
        ).count()
        first = snapshot_leave_balances(
            employees=[profile], year=2026, snapshot_date=snap_date
        )
        self.assertEqual(first["created_count"], expected_balance_count)
        self.assertEqual(first["updated_count"], 0)

        snap = LeaveBalanceSnapshot.objects.get(
            employee=profile,
            year=2026,
            snapshot_date=snap_date,
            leave_type=LeaveType.VACATION,
        )
        self.assertEqual(snap.allocated, 25)
        self.assertEqual(snap.used, 4)
        self.assertEqual(snap.carryover, 2)
        self.assertEqual(snap.remaining, 23)

        # Mutating the balance and re-snapshotting on the same date updates in place.
        LeaveBalance.objects.filter(
            employee=profile, year=2026, leave_type=LeaveType.VACATION
        ).update(used=10)
        second = snapshot_leave_balances(
            employees=[profile], year=2026, snapshot_date=snap_date
        )
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["updated_count"], expected_balance_count)
        snap.refresh_from_db()
        self.assertEqual(snap.used, 10)
        self.assertEqual(snap.remaining, 17)

    def test_read_helpers_return_expected_shapes(self):
        profile = self._create_profile("emp-read")
        self._make_request(
            profile, start=date(2026, 2, 2), end=date(2026, 2, 6)
        )
        self._make_request(
            profile,
            leave_type=LeaveType.SICK,
            start=date(2026, 2, 9),
            end=date(2026, 2, 10),
        )
        materialize_leave_monthly_aggregates()

        rows = monthly_breakdown(year=2026)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {r.leave_type for r in rows},
            {LeaveType.VACATION, LeaveType.SICK},
        )

        totals = yearly_totals_by_type(2026)
        self.assertEqual(totals[LeaveType.VACATION], 5)
        self.assertEqual(totals[LeaveType.SICK], 2)
        # Untouched types stay at zero.
        self.assertEqual(totals[LeaveType.WFH], 0)

    def test_refresh_leave_analytics_command_runs_end_to_end(self):
        profile = self._create_profile("emp-cmd")
        self._ensure_policy()
        self._ensure_balance(profile, used=3)
        self._make_request(
            profile, start=date(2026, 6, 1), end=date(2026, 6, 3)
        )

        call_command(
            "refresh_leave_analytics",
            "--year-from", "2026",
            "--year-to", "2026",
            "--snapshot-date", "2026-06-04",
            verbosity=0,
        )

        self.assertTrue(
            LeaveMonthlyAggregate.objects.filter(year=2026, month=6).exists()
        )
        self.assertTrue(
            LeaveBalanceSnapshot.objects.filter(
                employee=profile, snapshot_date=date(2026, 6, 4)
            ).exists()
        )
