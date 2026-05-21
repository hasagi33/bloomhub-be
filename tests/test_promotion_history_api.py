"""Tests for the promotion history API (BHB-463)."""

from datetime import date

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import PromotionHistory, Role, UserProfile


def _extract_results(payload):
    """Unwrap DRF's paginated envelope when present."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload


class PromotionHistoryAPITestCase(APITestCase):
    """End-to-end coverage for ``/api/promotion-history/``."""

    def setUp(self):
        self.junior = Role.objects.create(name="Junior Engineer")
        self.senior = Role.objects.create(name="Senior Engineer")

        self.emp_user = User.objects.create_user(
            username="employee", email="emp@test.com", password="pw"
        )
        self.other_user = User.objects.create_user(
            username="other", email="other@test.com", password="pw"
        )
        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pw", is_staff=True
        )
        self.emp = UserProfile.objects.get(user=self.emp_user)
        self.other = UserProfile.objects.get(user=self.other_user)

        self.emp_promo = PromotionHistory.objects.create(
            employee=self.emp,
            previous_role=self.junior,
            new_role=self.senior,
            date=date(2025, 6, 1),
            notes="Strong performance through H1.",
        )
        self.other_promo = PromotionHistory.objects.create(
            employee=self.other,
            previous_role=self.junior,
            new_role=self.senior,
            date=date(2025, 3, 15),
        )

    # ── auth ──

    def test_requires_authentication(self):
        response = self.client.get("/api/promotion-history/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── list scoping ──

    def test_employee_sees_only_own_promotions(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/promotion-history/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        ids = {r["id"] for r in results}
        self.assertEqual(ids, {self.emp_promo.id})

    def test_hr_sees_all_promotions(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/promotion-history/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        ids = {r["id"] for r in results}
        self.assertEqual(ids, {self.emp_promo.id, self.other_promo.id})

    def test_hr_can_filter_by_employee(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(f"/api/promotion-history/?employee={self.other.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.other_promo.id)

    def test_read_payload_includes_role_names(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/promotion-history/{self.emp_promo.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["employee_id"], self.emp.id)
        self.assertEqual(data["previous_role_name"], "Junior Engineer")
        self.assertEqual(data["new_role_name"], "Senior Engineer")
        self.assertEqual(data["notes"], "Strong performance through H1.")

    # ── create ──

    def test_hr_can_create_promotion(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            "/api/promotion-history/",
            {
                "employee_id": self.emp.id,
                "previous_role_id": self.junior.id,
                "new_role_id": self.senior.id,
                "date": "2026-01-10",
                "notes": "Promoted into the platform team.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data["employee_id"], self.emp.id)
        self.assertEqual(data["new_role_name"], "Senior Engineer")
        self.assertTrue(
            PromotionHistory.objects.filter(
                employee=self.emp, date=date(2026, 1, 10)
            ).exists()
        )

    def test_employee_cannot_create_promotion(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            "/api/promotion-history/",
            {"employee_id": self.emp.id, "date": "2026-01-10"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── update ──

    def test_hr_can_update_promotion(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.patch(
            f"/api/promotion-history/{self.emp_promo.id}/",
            {"notes": "Updated context."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["notes"], "Updated context.")
        self.emp_promo.refresh_from_db()
        self.assertEqual(self.emp_promo.notes, "Updated context.")

    def test_employee_cannot_update_promotion(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.patch(
            f"/api/promotion-history/{self.emp_promo.id}/",
            {"notes": "Hacked."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── delete ──

    def test_hr_can_delete_promotion(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.delete(f"/api/promotion-history/{self.emp_promo.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PromotionHistory.objects.filter(id=self.emp_promo.id).exists())

    def test_employee_cannot_delete_promotion(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.delete(f"/api/promotion-history/{self.emp_promo.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(PromotionHistory.objects.filter(id=self.emp_promo.id).exists())
