"""Tests for the training budget API and recalculation/notification flow."""

from datetime import UTC, date, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    Notification,
    Permission,
    Role,
    TrainingBudget,
    TrainingEntry,
    UserProfile,
)
from core.services.training_budget_service import recalculate_budget


def _grant(profile: UserProfile, module: str, action: str) -> None:
    perm = Permission.objects.get(module_name=module, feature_action=action)
    role = profile.role
    if role is None:
        role = Role.objects.create(name=f"role-{profile.pk}")
        profile.role = role
        profile.save(update_fields=["role"])
    role.permissions.add(perm)


def _refresh_user(user: User) -> User:
    """Return a freshly-loaded User so the cached `.profile` reflects current role."""
    return User.objects.get(pk=user.pk)


class TrainingBudgetAPITests(APITestCase):
    @staticmethod
    def _results(payload):
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        return payload

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.emp_user = User.objects.create_user(
            username="emp", email="emp@test.com", password="x"
        )
        self.hr_user = User.objects.create_user(
            username="hr",
            email="hr@test.com",
            password="x",
            is_staff=True,
        )
        self.emp = UserProfile.objects.get(user=self.emp_user)
        self.hr = UserProfile.objects.get(user=self.hr_user)
        _grant(self.emp, "Training", "track_own_budget")
        self.emp_user = _refresh_user(self.emp_user)
        self.hr_user = _refresh_user(self.hr_user)

    def test_hr_can_create_budget(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            "/api/training-budgets/",
            {
                "employee_id": self.emp.pk,
                "fiscal_year": 2026,
                "allocated_budget": "1000.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        budget = TrainingBudget.objects.get(employee=self.emp, fiscal_year=2026)
        self.assertEqual(budget.allocated_budget, Decimal("1000.00"))

    def test_employee_can_read_own_budget(self):
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("500.00")
        )
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-budgets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = self._results(response.json())
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["fiscal_year"]), 2026)
        rows = self._results(response.json())
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["fiscal_year"]), 2026)

    def test_employee_cannot_create_budget(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            "/api/training-budgets/",
            {
                "employee_id": self.emp.pk,
                "fiscal_year": 2026,
                "allocated_budget": "500.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_me_endpoint_returns_placeholder_when_missing(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-budgets/me/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(int(data["fiscal_year"]), 2026)
        self.assertEqual(Decimal(data["allocated_budget"]), Decimal("0.00"))


class TrainingBudgetRecalcAndAlertTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.emp_user = User.objects.create_user(
            username="emp", email="emp@test.com", password="x"
        )
        self.emp = UserProfile.objects.get(user=self.emp_user)

    def test_recalculate_aggregates_costs(self):
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("1000.00")
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="A",
            provider="P",
            training_date=date(2026, 3, 1),
            cost=Decimal("300.00"),
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="B",
            provider="P",
            training_date=date(2026, 4, 1),
            cost=Decimal("200.00"),
        )

        recalculate_budget(self.emp, 2026)
        budget = TrainingBudget.objects.get(employee=self.emp, fiscal_year=2026)
        self.assertEqual(budget.used_budget, Decimal("500.00"))

    def test_threshold_notification_fires_once(self):
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("1000.00")
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="A",
            provider="P",
            training_date=date(2026, 3, 1),
            cost=Decimal("800.00"),
        )
        recalculate_budget(self.emp, 2026)
        recalculate_budget(self.emp, 2026)
        notifs = Notification.objects.filter(
            recipient=self.emp, module=Notification.Module.TRAINING
        )
        self.assertEqual(notifs.count(), 1)
        self.assertEqual(notifs.first().type, Notification.Type.WARNING)

    def test_exceeded_alert_marked_as_alert(self):
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("100.00")
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="A",
            provider="P",
            training_date=date(2026, 3, 1),
            cost=Decimal("150.00"),
        )
        recalculate_budget(self.emp, 2026)
        notif = Notification.objects.filter(
            recipient=self.emp, module=Notification.Module.TRAINING
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.type, Notification.Type.ALERT)

    def test_future_dated_entry_does_not_count_toward_used(self):
        """An entry with training_date > today is excluded from used_budget."""
        from datetime import timedelta

        from django.utils import timezone

        future = timezone.now().date() + timedelta(days=365)
        TrainingBudget.objects.create(
            employee=self.emp,
            fiscal_year=future.year,
            allocated_budget=Decimal("1000.00"),
        )
        # Bypass serializer validation by writing directly to ORM.
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="Future",
            provider="P",
            training_date=future,
            cost=Decimal("500.00"),
        )
        recalculate_budget(self.emp, future.year)
        budget = TrainingBudget.objects.get(employee=self.emp, fiscal_year=future.year)
        self.assertEqual(budget.used_budget, Decimal("0.00"))

    def test_completed_at_year_takes_priority_over_training_date(self):
        """Entries with completed_at are bucketed by completed_at year."""
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("1000.00")
        )
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2025, allocated_budget=Decimal("1000.00")
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="Started 2025, completed 2026",
            provider="P",
            training_date=date(2025, 12, 20),
            completed_at=datetime(2026, 1, 10, tzinfo=UTC),
            cost=Decimal("400.00"),
        )

        recalculate_budget(self.emp, 2025)
        recalculate_budget(self.emp, 2026)

        b2025 = TrainingBudget.objects.get(employee=self.emp, fiscal_year=2025)
        b2026 = TrainingBudget.objects.get(employee=self.emp, fiscal_year=2026)
        self.assertEqual(b2025.used_budget, Decimal("0.00"))
        self.assertEqual(b2026.used_budget, Decimal("400.00"))

    def test_lowering_allocation_via_patch_triggers_threshold_alert(self):
        """HR shrinks allocation below 80% of existing usage → alert fires."""
        budget = TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("1000.00")
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="A",
            provider="P",
            training_date=date(2026, 3, 1),
            cost=Decimal("500.00"),
        )
        recalculate_budget(self.emp, 2026)
        # 50% used → no notification yet.
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.emp, module=Notification.Module.TRAINING
            ).exists()
        )

        hr_user = User.objects.create_user(
            username="hr-patch", email="hrp@test.com", password="x", is_staff=True
        )
        self.client.force_authenticate(user=hr_user)
        response = self.client.patch(
            f"/api/training-budgets/{budget.pk}/",
            {"allocated_budget": "500.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        notifs = Notification.objects.filter(
            recipient=self.emp, module=Notification.Module.TRAINING
        )
        self.assertEqual(notifs.count(), 1)
        # 500/500 = 100% → exceeded category is borderline; ALERT only when
        # used > allocated. Here used == allocated → WARNING is correct.
        self.assertEqual(notifs.first().type, Notification.Type.WARNING)

    def test_raising_allocation_via_patch_resets_threshold(self):
        """When usage drops back under 80%, threshold_notified_at is cleared."""
        budget = TrainingBudget.objects.create(
            employee=self.emp,
            fiscal_year=2026,
            allocated_budget=Decimal("100.00"),
        )
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="A",
            provider="P",
            training_date=date(2026, 3, 1),
            cost=Decimal("90.00"),
        )
        recalculate_budget(self.emp, 2026)
        budget.refresh_from_db()
        self.assertIsNotNone(budget.threshold_notified_at)

        hr_user = User.objects.create_user(
            username="hr-raise", email="hrr@test.com", password="x", is_staff=True
        )
        self.client.force_authenticate(user=hr_user)
        response = self.client.patch(
            f"/api/training-budgets/{budget.pk}/",
            {"allocated_budget": "1000.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        budget.refresh_from_db()
        self.assertIsNone(budget.threshold_notified_at)

    def test_creating_budget_when_entries_already_exist_syncs_used(self):
        """POST /training-budgets/ should pick up existing entry costs."""
        TrainingEntry.objects.create(
            employee=self.emp,
            course_title="Pre-existing",
            provider="P",
            training_date=date(2026, 2, 1),
            cost=Decimal("250.00"),
        )
        hr_user = User.objects.create_user(
            username="hr-init", email="hri@test.com", password="x", is_staff=True
        )
        self.client.force_authenticate(user=hr_user)
        response = self.client.post(
            "/api/training-budgets/",
            {
                "employee_id": self.emp.pk,
                "fiscal_year": 2026,
                "allocated_budget": "1000.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(response.json()["used_budget"]), Decimal("250.00"))

    def test_entry_create_endpoint_returns_budget_warning(self):
        TrainingBudget.objects.create(
            employee=self.emp, fiscal_year=2026, allocated_budget=Decimal("100.00")
        )
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            "/api/training-entries/",
            {
                "course_title": "Big course",
                "provider": "P",
                "training_date": "2026-03-01",
                "training_type": "course",
                "cost": "90.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("budget_warning", response.json())
        self.assertEqual(
            response.json()["budget_warning"]["level"], "approaching_limit"
        )
