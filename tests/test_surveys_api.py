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
        self.regular_profile, _ = UserProfile.objects.get_or_create(
            user=self.regular_user
        )

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

    def test_list_hides_forbidden_surveys_from_blocked_users(self):
        blocked = Survey.objects.create(title="Blocked", status="active")
        blocked.forbidden_users.add(self.regular_profile)
        Survey.objects.create(title="OpenToAll", status="active")
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.get("/api/surveys/")
        titles = [s["title"] for s in resp.data]
        self.assertIn("OpenToAll", titles)
        self.assertNotIn("Blocked", titles)

    def test_list_mine_filter_returns_only_own_surveys(self):
        # hr_user creates two surveys; another HR user creates one.
        Survey.objects.create(title="Mine 1", created_by=self.hr_profile)
        Survey.objects.create(title="Mine 2", created_by=self.hr_profile)

        other_hr = User.objects.create_user(
            username="hr2", email="hr2@test.com", password="pass", is_staff=True
        )
        other_profile = UserProfile.objects.get(user=other_hr)
        other_profile.role = self.hr_role
        other_profile.save()
        Survey.objects.create(title="Not Mine", created_by=other_profile)

        self.client.force_authenticate(user=self.hr_user)
        all_resp = self.client.get("/api/surveys/")
        mine_resp = self.client.get("/api/surveys/?mine=true")
        self.assertEqual(all_resp.status_code, 200)
        self.assertEqual(mine_resp.status_code, 200)
        self.assertGreaterEqual(len(all_resp.data), 3)
        mine_titles = [s["title"] for s in mine_resp.data]
        self.assertIn("Mine 1", mine_titles)
        self.assertIn("Mine 2", mine_titles)
        self.assertNotIn("Not Mine", mine_titles)

    def test_cannot_edit_survey_past_end_date(self):
        from datetime import date, timedelta

        survey = Survey.objects.create(
            title="Locked",
            is_anonymous=False,
            end_date=date.today() - timedelta(days=2),
        )
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.patch(
            f"/api/surveys/{survey.id}/",
            {"title": "Renamed"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        survey.refresh_from_db()
        self.assertEqual(survey.title, "Locked")

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
