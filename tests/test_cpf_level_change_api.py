"""Tests for the CPF level advancement tracking API (BHB-464)."""

from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import (
    CPFChangeSource,
    CPFProgressionEventType,
    ReviewOutcome,
    ReviewStatus,
    ReviewType,
)
from core.models import CPFLevelChange, PerformanceReview


def _extract_results(payload):
    """Unwrap DRF's paginated envelope when present."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload


class CPFLevelChangeAPITestCase(APITestCase):
    """End-to-end coverage for ``/api/cpf-level-changes/``."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.emp_user = User.objects.create_user(
            username="employee", email="emp@test.com", password="pw"
        )
        self.other_user = User.objects.create_user(
            username="other", email="other@test.com", password="pw"
        )
        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pw", is_staff=True
        )
        self.emp = self.emp_user.profile
        self.other = self.other_user.profile
        self.emp.cpf_level = "L3"
        self.emp.save(update_fields=["cpf_level"])

        self.emp_change = CPFLevelChange.objects.create(
            employee=self.emp,
            previous_level="L2",
            new_level="L3",
            effective_date=date(2025, 6, 1),
            cpf_score=72,
            notes="Promoted after H1 review.",
        )
        self.other_change = CPFLevelChange.objects.create(
            employee=self.other,
            previous_level="L1",
            new_level="L2",
            effective_date=date(2025, 3, 15),
        )

    # ── auth ──

    def test_requires_authentication(self):
        response = self.client.get("/api/cpf-level-changes/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── list scoping ──

    def test_employee_sees_only_own_changes(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/cpf-level-changes/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {r["id"] for r in _extract_results(response.json())}
        self.assertEqual(ids, {self.emp_change.id})

    def test_hr_sees_all_changes(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/cpf-level-changes/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {r["id"] for r in _extract_results(response.json())}
        self.assertEqual(ids, {self.emp_change.id, self.other_change.id})

    def test_hr_can_filter_by_employee(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(f"/api/cpf-level-changes/?employee={self.other.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.other_change.id)

    def test_read_payload_fields(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/cpf-level-changes/{self.emp_change.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["employee_id"], self.emp.id)
        self.assertEqual(data["previous_level"], "L2")
        self.assertEqual(data["new_level"], "L3")
        self.assertEqual(data["cpf_score"], 72)
        self.assertEqual(data["source"], CPFChangeSource.MANUAL.value)
        self.assertEqual(data["source_display"], CPFChangeSource.MANUAL.label)

    # ── create ──

    def test_hr_can_create_change(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            "/api/cpf-level-changes/",
            {
                "employee_id": self.emp.id,
                "previous_level": "L3",
                "new_level": "L4",
                "effective_date": "2026-01-10",
                "cpf_score": 85,
                "notes": "Advanced to L4.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data["new_level"], "L4")
        created = CPFLevelChange.objects.get(id=data["id"])
        self.assertEqual(created.recorded_by, self.hr_user.profile)

    def test_employee_cannot_create_change(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            "/api/cpf-level-changes/",
            {
                "employee_id": self.emp.id,
                "new_level": "L4",
                "effective_date": "2026-01-10",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_rejects_out_of_range_score(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            "/api/cpf-level-changes/",
            {
                "employee_id": self.emp.id,
                "new_level": "L4",
                "effective_date": "2026-01-10",
                "cpf_score": 150,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── update / delete ──

    def test_hr_can_update_change(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.patch(
            f"/api/cpf-level-changes/{self.emp_change.id}/",
            {"notes": "Updated context."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["notes"], "Updated context.")

    def test_employee_cannot_update_change(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.patch(
            f"/api/cpf-level-changes/{self.emp_change.id}/",
            {"notes": "Hacked."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_hr_can_delete_change(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.delete(f"/api/cpf-level-changes/{self.emp_change.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CPFLevelChange.objects.filter(id=self.emp_change.id).exists())

    def test_employee_cannot_delete_change(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.delete(f"/api/cpf-level-changes/{self.emp_change.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── progression timeline ──

    def test_progression_combines_changes_and_reviews(self):
        PerformanceReview.objects.create(
            employee=self.emp,
            review_type=ReviewType.QUARTERLY,
            scheduled_date=date(2025, 9, 1),
            status=ReviewStatus.COMPLETED,
            outcome=ReviewOutcome.EXCEEDS_EXPECTATIONS,
            cpf_current_level="L3",
            cpf_recommended_level="L4",
            cpf_score=88,
            completed_at=timezone.make_aware(timezone.datetime(2025, 9, 2, 12, 0)),
        )
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/cpf-level-changes/progression/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["employee_id"], self.emp.id)
        self.assertEqual(data["current_level"], "L3")
        timeline = data["timeline"]
        self.assertEqual(len(timeline), 2)
        # ascending by date
        self.assertEqual(timeline[0]["date"], "2025-06-01")
        self.assertEqual(
            timeline[0]["event_type"], CPFProgressionEventType.LEVEL_CHANGE.value
        )
        self.assertEqual(timeline[1]["date"], "2025-09-02")
        self.assertEqual(
            timeline[1]["event_type"],
            CPFProgressionEventType.REVIEW_ASSESSMENT.value,
        )
        self.assertEqual(timeline[1]["new_level"], "L4")

    def test_progression_excludes_reviews_without_cpf_data(self):
        PerformanceReview.objects.create(
            employee=self.emp,
            review_type=ReviewType.QUARTERLY,
            scheduled_date=date(2025, 9, 1),
            status=ReviewStatus.COMPLETED,
        )
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/cpf-level-changes/progression/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()["timeline"]), 1)

    def test_progression_employee_cannot_query_others(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(
            f"/api/cpf-level-changes/progression/?employee={self.other.id}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # employee param ignored for non-HR — returns own timeline
        self.assertEqual(response.json()["employee_id"], self.emp.id)

    def test_progression_hr_can_query_any_employee(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(
            f"/api/cpf-level-changes/progression/?employee={self.other.id}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["employee_id"], self.other.id)

    def test_progression_hr_missing_employee_returns_404(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(
            "/api/cpf-level-changes/progression/?employee=999999"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── current level sync ──

    def test_recording_change_updates_profile_current_level(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            "/api/cpf-level-changes/",
            {
                "employee_id": self.emp.id,
                "new_level": "L5",
                "effective_date": "2026-02-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.cpf_level, "L5")
        prog = self.client.get(
            f"/api/cpf-level-changes/progression/?employee={self.emp.id}"
        )
        self.assertEqual(prog.json()["current_level"], "L5")

    def test_backdated_change_does_not_override_current_level(self):
        # A change older than the existing latest must not become current.
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            "/api/cpf-level-changes/",
            {
                "employee_id": self.emp.id,
                "new_level": "L1",
                "effective_date": "2024-01-01",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.emp.refresh_from_db()
        # latest change is still emp_change (2025-06-01 → L3)
        self.assertEqual(self.emp.cpf_level, "L3")
