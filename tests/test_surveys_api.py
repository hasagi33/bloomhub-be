"""Tests for BHB-451 — Survey CRUD, anonymity, and close behaviour."""

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import SurveyStatus
from core.models import (
    Question,
    Role,
    Survey,
    UserProfile,
)
from core.models import (
    Response as SurveyResponse,
)


class SurveyAPITests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.hr_role, _ = Role.objects.get_or_create(name="HR")
        # Use staff flag for the write-permission path — IsHROrStaffForSurveyWrite
        # accepts staff/superuser without needing the Permission bitmap setup.
        self.hr_user = User.objects.create_user(
            username="hr_user",
            email="hr@test.com",
            password="pass",
            is_staff=True,
        )
        self.hr_profile = UserProfile.objects.get(user=self.hr_user)
        self.hr_profile.role = self.hr_role
        self.hr_profile.save()

        self.regular_user = User.objects.create_user(
            username="regular", email="r@test.com", password="pass"
        )
        UserProfile.objects.get_or_create(user=self.regular_user)

    # ── Create ──────────────────────────────────────────────────────────────

    def test_hr_can_create_survey_with_nested_questions(self):
        self.client.force_authenticate(user=self.hr_user)
        payload = {
            "title": "Q3 Pulse",
            "description": "Short pulse survey.",
            "is_anonymous": True,
            "questions": [
                {"text": "How are you feeling?", "type": "scale"},
                {
                    "text": "Pick one",
                    "type": "choice",
                    "options": ["A", "B", "C"],
                },
                {"text": "Anything else?", "type": "text"},
            ],
        }
        resp = self.client.post("/api/surveys/", payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["title"], "Q3 Pulse")
        self.assertTrue(resp.data["is_anonymous"])
        self.assertEqual(resp.data["status"], SurveyStatus.DRAFT)
        self.assertEqual(len(resp.data["questions"]), 3)
        self.assertEqual(Survey.objects.count(), 1)
        self.assertEqual(Question.objects.count(), 3)

    def test_regular_user_cannot_create_survey(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.post(
            "/api/surveys/",
            {"title": "Nope", "is_anonymous": False},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_list(self):
        resp = self.client.get("/api/surveys/")
        # DRF returns 401 when no auth credentials are supplied.
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_choice_question_requires_options(self):
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.post(
            "/api/surveys/",
            {
                "title": "Bad",
                "is_anonymous": False,
                "questions": [{"text": "Pick", "type": "choice", "options": []}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── List / Retrieve ─────────────────────────────────────────────────────

    def test_authenticated_user_can_list_and_retrieve(self):
        survey = Survey.objects.create(title="Open", is_anonymous=False)
        Question.objects.create(survey=survey, text="Q1", type="text", order=0)
        self.client.force_authenticate(user=self.regular_user)

        list_resp = self.client.get("/api/surveys/")
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_resp.data), 1)

        detail_resp = self.client.get(f"/api/surveys/{survey.id}/")
        self.assertEqual(detail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(detail_resp.data["questions"]), 1)

    # ── Update ──────────────────────────────────────────────────────────────

    def test_patch_replaces_questions_when_provided(self):
        survey = Survey.objects.create(title="Old", is_anonymous=False)
        Question.objects.create(survey=survey, text="Old Q", type="text", order=0)
        self.client.force_authenticate(user=self.hr_user)

        resp = self.client.patch(
            f"/api/surveys/{survey.id}/",
            {
                "title": "New",
                "questions": [
                    {"text": "New Q1", "type": "text"},
                    {"text": "New Q2", "type": "text"},
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        survey.refresh_from_db()
        self.assertEqual(survey.title, "New")
        self.assertEqual(survey.questions.count(), 2)
        self.assertFalse(survey.questions.filter(text="Old Q").exists())

    # ── Close ───────────────────────────────────────────────────────────────

    def test_close_survey(self):
        survey = Survey.objects.create(title="Active", is_anonymous=False)
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.post(f"/api/surveys/{survey.id}/close/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        survey.refresh_from_db()
        self.assertEqual(survey.status, SurveyStatus.CLOSED)

    def test_cannot_close_twice(self):
        survey = Survey.objects.create(
            title="Closed", is_anonymous=False, status=SurveyStatus.CLOSED
        )
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.post(f"/api/surveys/{survey.id}/close/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Delete ──────────────────────────────────────────────────────────────

    def test_delete_survey_without_responses(self):
        survey = Survey.objects.create(title="To delete", is_anonymous=False)
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.delete(f"/api/surveys/{survey.id}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_cannot_delete_survey_with_responses(self):
        survey = Survey.objects.create(title="Has data", is_anonymous=False)
        SurveyResponse.objects.create(survey=survey)
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.delete(f"/api/surveys/{survey.id}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Anonymity safeguard ─────────────────────────────────────────────────

    def test_anonymous_survey_strips_respondent_on_save(self):
        survey = Survey.objects.create(title="Anon", is_anonymous=True)
        r = SurveyResponse(survey=survey, respondent=self.hr_profile)
        r.save()
        r.refresh_from_db()
        self.assertIsNone(r.respondent_id)

    def test_named_survey_keeps_respondent(self):
        survey = Survey.objects.create(title="Named", is_anonymous=False)
        r = SurveyResponse(survey=survey, respondent=self.hr_profile)
        r.save()
        r.refresh_from_db()
        self.assertEqual(r.respondent_id, self.hr_profile.id)

    # ── Add question via action ─────────────────────────────────────────────

    def test_add_question_appends(self):
        survey = Survey.objects.create(title="Add Qs", is_anonymous=False)
        Question.objects.create(survey=survey, text="Existing", type="text", order=0)
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.post(
            f"/api/surveys/{survey.id}/questions/",
            {"text": "Appended", "type": "text"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["order"], 1)
        self.assertEqual(survey.questions.count(), 2)
