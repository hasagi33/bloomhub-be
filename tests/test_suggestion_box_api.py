"""Tests for BHB-454 — Suggestion box API."""

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import SuggestionStatus
from core.models import Suggestion, UserProfile


class SuggestionBoxAPITests(APITestCase):
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
        UserProfile.objects.get(user=self.hr_user)

        self.regular_user = User.objects.create_user(
            username="alice", email="a@test.com", password="pass"
        )
        self.profile = UserProfile.objects.get(user=self.regular_user)

    # ── Submit ─────────────────────────────────────────────────────────────

    def test_authenticated_user_can_submit(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.post(
            "/api/suggestions/",
            {"category": "hr", "text": "More flexible hours"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        s = Suggestion.objects.get()
        self.assertEqual(s.text, "More flexible hours")
        self.assertEqual(s.category, "hr")
        self.assertEqual(s.status, SuggestionStatus.NEW)
        self.assertEqual(s.employee_id, self.profile.id)

    def test_anonymous_submission_strips_employee(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.post(
            "/api/suggestions/",
            {"text": "Better coffee", "is_anonymous": True},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        s = Suggestion.objects.get()
        self.assertIsNone(s.employee_id)

    def test_unauthenticated_cannot_submit(self):
        resp = self.client.post("/api/suggestions/", {"text": "no auth"}, format="json")
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_empty_text_rejected(self):
        self.client.force_authenticate(user=self.regular_user)
        for bad in ("", "   ", "\n\t"):
            resp = self.client.post("/api/suggestions/", {"text": bad}, format="json")
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── List + status update ───────────────────────────────────────────────

    def test_regular_user_cannot_list(self):
        Suggestion.objects.create(employee=self.profile, text="Idea")
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.get("/api/suggestions/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_hr_can_list(self):
        Suggestion.objects.create(employee=self.profile, text="Idea 1")
        Suggestion.objects.create(text="Anonymous idea")  # no employee
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get("/api/suggestions/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 2)
        names = {row["employee_name"] for row in resp.data}
        self.assertIn("Anonymous", names)

    def test_regular_user_cannot_update_status(self):
        s = Suggestion.objects.create(employee=self.profile, text="Idea")
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.patch(
            f"/api/suggestions/{s.id}/",
            {"status": SuggestionStatus.UNDER_REVIEW},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_hr_can_update_status(self):
        s = Suggestion.objects.create(employee=self.profile, text="Idea")
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.patch(
            f"/api/suggestions/{s.id}/",
            {"status": SuggestionStatus.IMPLEMENTED},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        s.refresh_from_db()
        self.assertEqual(s.status, SuggestionStatus.IMPLEMENTED)

    def test_invalid_status_rejected(self):
        s = Suggestion.objects.create(employee=self.profile, text="Idea")
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.patch(
            f"/api/suggestions/{s.id}/",
            {"status": "not_a_real_status"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
