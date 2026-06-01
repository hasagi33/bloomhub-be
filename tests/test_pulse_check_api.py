"""Tests for BHB-452 — Pulse Check API."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import PulseCheck, UserProfile


class PulseCheckAPITests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.hr_user = User.objects.create_user(
            username="hr_user",
            email="hr@test.com",
            password="pass",
            is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            username="alice", email="a@test.com", password="pass"
        )
        self.profile = UserProfile.objects.get(user=self.regular_user)
        UserProfile.objects.get(user=self.hr_user)

    # ── Submit ──────────────────────────────────────────────────────────────

    def test_authenticated_user_can_submit_pulse(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.post("/api/pulse-checks/", {"value": 4}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(PulseCheck.objects.count(), 1)
        pc = PulseCheck.objects.first()
        self.assertEqual(pc.value, 4)
        self.assertEqual(pc.employee_id, self.profile.id)

    def test_unauthenticated_cannot_submit(self):
        resp = self.client.post("/api/pulse-checks/", {"value": 3}, format="json")
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_value_must_be_within_range(self):
        self.client.force_authenticate(user=self.regular_user)
        for bad in (0, 6, -1, 100):
            resp = self.client.post("/api/pulse-checks/", {"value": bad}, format="json")
            self.assertEqual(
                resp.status_code, status.HTTP_400_BAD_REQUEST, f"value={bad}"
            )

    def test_user_can_submit_multiple_pulses(self):
        # No dedup — pulse checks accumulate over time for trend tracking.
        self.client.force_authenticate(user=self.regular_user)
        for v in (1, 3, 5):
            r = self.client.post("/api/pulse-checks/", {"value": v}, format="json")
            self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PulseCheck.objects.count(), 3)

    # ── List + Summary permissions ──────────────────────────────────────────

    def test_regular_user_cannot_list(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.get("/api/pulse-checks/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_regular_user_cannot_view_summary(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.get("/api/pulse-checks/summary/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_hr_can_list(self):
        PulseCheck.objects.create(employee=self.profile, value=4)
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get("/api/pulse-checks/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    # ── Summary aggregation ────────────────────────────────────────────────

    def test_summary_returns_average_and_distribution(self):
        for v in (5, 4, 4, 3, 1):
            PulseCheck.objects.create(employee=self.profile, value=v)
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get("/api/pulse-checks/summary/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 5)
        self.assertEqual(resp.data["average"], round((5 + 4 + 4 + 3 + 1) / 5, 2))
        dist = {row["value"]: row["count"] for row in resp.data["distribution"]}
        self.assertEqual(dist, {1: 1, 2: 0, 3: 1, 4: 2, 5: 1})

    def test_summary_window_filters_old_entries(self):
        old = PulseCheck.objects.create(employee=self.profile, value=1)
        PulseCheck.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        PulseCheck.objects.create(employee=self.profile, value=5)

        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get("/api/pulse-checks/summary/?days=7")
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(resp.data["average"], 5.0)

        resp_all = self.client.get("/api/pulse-checks/summary/?days=60")
        self.assertEqual(resp_all.data["count"], 2)

    def test_summary_empty_window_returns_zero(self):
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get("/api/pulse-checks/summary/?days=7")
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["average"], 0.0)
