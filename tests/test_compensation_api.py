from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import BonusType, EmploymentStatus, LeaveRequestStatus, LeaveType
from core.models import (
    BonusRecord,
    LeaveRequest,
    PayrollSnapshot,
    PerformanceReview,
    Permission,
    SalaryRecord,
    UserProfile,
)


class CompensationAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def _make_user(self, username: str, salary: Decimal | None = None) -> UserProfile:
        user = User.objects.create_user(
            username=username,
            email=f"{username}@test.com",
            password="pass",
            first_name=username.title(),
            last_name="User",
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.employment_status = EmploymentStatus.ACTIVE
        profile.full_name = f"{username.title()} User"
        profile.save()
        if salary is not None:
            SalaryRecord.objects.create(
                user_profile=profile,
                amount=salary,
                effective_date=date.today() - timedelta(days=30),
            )
        return profile

    def _grant_hr(self, profile: UserProfile):
        perm, _ = Permission.objects.get_or_create(
            module_name="Employee Profiles", feature_action="view_all_profiles"
        )
        profile.add_permission(perm)

    def setUp(self):
        self.hr_profile = self._make_user("hr", Decimal("4000"))
        self._grant_hr(self.hr_profile)
        self.alice = self._make_user("alice", Decimal("2500"))
        self.bob = self._make_user("bob", Decimal("3500"))

    # ── Bonuses ────────────────────────────────────────────────────────────

    def test_hr_can_create_bonus(self):
        self.client.force_authenticate(self.hr_profile.user)
        res = self.client.post(
            "/api/bonuses/",
            {
                "user_profile": self.alice.id,
                "bonus_type": BonusType.PERFORMANCE,
                "amount": "1200.00",
                "effective_date": date.today().isoformat(),
                "reason": "Q1 perf",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(BonusRecord.objects.count(), 1)
        row = BonusRecord.objects.first()
        self.assertEqual(row.currency, "BAM")
        self.assertEqual(row.amount, Decimal("1200.00"))

    def test_non_hr_cannot_create_bonus(self):
        self.client.force_authenticate(self.alice.user)
        res = self.client.post(
            "/api/bonuses/",
            {
                "user_profile": self.alice.id,
                "bonus_type": BonusType.SPOT,
                "amount": "100.00",
                "effective_date": date.today().isoformat(),
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_hr_lists_only_own_bonuses(self):
        BonusRecord.objects.create(
            user_profile=self.alice,
            bonus_type=BonusType.PERFORMANCE,
            amount=Decimal("300"),
            effective_date=date.today(),
        )
        BonusRecord.objects.create(
            user_profile=self.bob,
            bonus_type=BonusType.SPOT,
            amount=Decimal("200"),
            effective_date=date.today(),
        )
        self.client.force_authenticate(self.alice.user)
        res = self.client.get("/api/bonuses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.json()
        if isinstance(results, dict) and "results" in results:
            results = results["results"]
        for row in results:
            self.assertEqual(row["user_profile"], self.alice.id)

    def test_employee_bonuses_endpoint_self_access(self):
        BonusRecord.objects.create(
            user_profile=self.alice,
            bonus_type=BonusType.REFERRAL,
            amount=Decimal("500"),
            effective_date=date.today(),
        )
        self.client.force_authenticate(self.alice.user)
        res = self.client.get(f"/api/employees/{self.alice.id}/bonuses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.json()), 1)

    def test_employee_bonuses_endpoint_forbidden_for_other(self):
        self.client.force_authenticate(self.alice.user)
        res = self.client.get(f"/api/employees/{self.bob.id}/bonuses/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # ── Overview ───────────────────────────────────────────────────────────

    def test_overview_hr_only(self):
        self.client.force_authenticate(self.alice.user)
        res = self.client.get("/api/compensation/overview/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_overview_stats_and_bands(self):
        self.client.force_authenticate(self.hr_profile.user)
        res = self.client.get("/api/compensation/overview/")
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)
        body = res.json()
        self.assertIn("stats", body)
        self.assertIn("bands", body)
        self.assertIn("mix", body)
        self.assertIn("employees", body)
        # 3 active employees: 4000 + 2500 + 3500 = 10000
        self.assertEqual(body["stats"]["totalEmployees"], 3)
        self.assertAlmostEqual(body["stats"]["totalMonthly"], 10000.0, places=2)
        self.assertAlmostEqual(body["stats"]["avgSalary"], 10000.0 / 3, places=2)
        self.assertAlmostEqual(body["stats"]["medianSalary"], 3500.0, places=2)
        labels = {b["label"] for b in body["bands"]}
        self.assertIn("BAM 2.5k–3.5k", labels)

    def test_overview_pending_overdue_reviews(self):
        PerformanceReview.objects.create(
            employee=self.alice,
            scheduled_date=date.today() + timedelta(days=5),
            status="scheduled",
        )
        PerformanceReview.objects.create(
            employee=self.bob,
            scheduled_date=date.today() - timedelta(days=10),
            status="scheduled",
        )
        self.client.force_authenticate(self.hr_profile.user)
        res = self.client.get("/api/compensation/overview/")
        body = res.json()
        self.assertEqual(body["stats"]["pendingReviews"], 2)
        self.assertEqual(body["stats"]["overdueReviews"], 1)

    def test_compensation_status_pto_from_leave(self):
        LeaveRequest.objects.create(
            employee=self.alice,
            leave_type=LeaveType.VACATION,
            start_date=date.today() - timedelta(days=1),
            end_date=date.today() + timedelta(days=2),
            reason="trip",
            status=LeaveRequestStatus.APPROVED,
        )
        from core.services.compensation_service import compute_compensation_status

        self.assertEqual(compute_compensation_status(self.alice), "PTO")
        self.assertEqual(compute_compensation_status(self.bob), "Active")

    def test_bonus_pct_calculation(self):
        # Salary 2500; bonus 6000 in last 12mo → 6000/12/2500*100 = 20.0
        BonusRecord.objects.create(
            user_profile=self.alice,
            bonus_type=BonusType.PERFORMANCE,
            amount=Decimal("6000"),
            effective_date=date.today() - timedelta(days=30),
        )
        from core.services.compensation_service import compute_bonus_pct

        self.assertAlmostEqual(compute_bonus_pct(self.alice), 20.0, places=2)

    # ── Snapshot command ───────────────────────────────────────────────────

    def test_snapshot_command_persists_aggregate(self):
        call_command("snapshot_payroll", verbosity=0)
        snap = PayrollSnapshot.objects.first()
        self.assertIsNotNone(snap)
        self.assertEqual(snap.headcount, 3)
        self.assertEqual(snap.total_monthly, Decimal("10000.00"))
        self.assertEqual(snap.median_salary, Decimal("3500.00"))

    def test_snapshot_command_idempotent(self):
        call_command("snapshot_payroll", verbosity=0)
        call_command("snapshot_payroll", verbosity=0)
        self.assertEqual(PayrollSnapshot.objects.count(), 1)

    def test_overview_delta_uses_snapshot(self):
        # Snapshot last month: total_monthly = 8000 → current 10000 → +25%
        last_month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        PayrollSnapshot.objects.create(
            snapshot_date=last_month,
            total_monthly=Decimal("8000"),
            avg_salary=Decimal("2666.67"),
            median_salary=Decimal("2500"),
            headcount=3,
        )
        self.client.force_authenticate(self.hr_profile.user)
        res = self.client.get("/api/compensation/overview/")
        self.assertAlmostEqual(res.json()["stats"]["monthlyDeltaPct"], 25.0, places=1)

    # ── BAM currency preservation ──────────────────────────────────────────

    def test_bonus_amount_no_rounding(self):
        BonusRecord.objects.create(
            user_profile=self.alice,
            bonus_type=BonusType.SPOT,
            amount=Decimal("1234.56"),
            effective_date=date.today(),
        )
        row = BonusRecord.objects.get(user_profile=self.alice)
        self.assertEqual(row.amount, Decimal("1234.56"))
        self.assertEqual(row.currency, "BAM")
