"""Tests for BHB-453 — Survey response submission endpoint."""

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import SurveyStatus
from core.models import (
    Answer,
    Question,
    Survey,
    UserProfile,
)
from core.models import (
    Response as SurveyResponse,
)


class SurveyResponseSubmissionTests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice", email="a@test.com", password="pass"
        )
        self.profile = UserProfile.objects.get(user=self.user)

        self.other_user = User.objects.create_user(
            username="bob", email="b@test.com", password="pass"
        )
        self.other_profile = UserProfile.objects.get(user=self.other_user)

        self.survey = Survey.objects.create(
            title="Active",
            is_anonymous=False,
            status=SurveyStatus.ACTIVE,
        )
        self.q1 = Question.objects.create(
            survey=self.survey, text="Scale?", type="scale", order=0
        )
        self.q2 = Question.objects.create(
            survey=self.survey,
            text="Pick?",
            type="choice",
            order=1,
            options=["A", "B"],
        )
        self.q3 = Question.objects.create(
            survey=self.survey, text="Free text?", type="text", order=2
        )

    def _submit(self, scale="4", choice="A", text="ok"):
        return self.client.post(
            f"/api/surveys/{self.survey.id}/responses/",
            {
                "answers": [
                    {"question_id": self.q1.id, "value": scale},
                    {"question_id": self.q2.id, "value": choice},
                    {"question_id": self.q3.id, "value": text},
                ]
            },
            format="json",
        )

    def test_authenticated_user_can_submit_response(self):
        self.client.force_authenticate(user=self.user)
        resp = self._submit()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(SurveyResponse.objects.count(), 1)
        self.assertEqual(Answer.objects.count(), 3)
        saved = SurveyResponse.objects.first()
        self.assertEqual(saved.respondent_id, self.profile.id)

    def test_unauthenticated_blocked(self):
        resp = self._submit()
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_draft_survey_rejects_submissions(self):
        draft = Survey.objects.create(
            title="Draft", is_anonymous=False, status=SurveyStatus.DRAFT
        )
        Question.objects.create(survey=draft, text="?", type="text", order=0)
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"/api/surveys/{draft.id}/responses/",
            {"answers": [{"question_id": draft.questions.first().id, "value": "x"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_closed_survey_rejects_submissions(self):
        self.survey.status = SurveyStatus.CLOSED
        self.survey.save()
        self.client.force_authenticate(user=self.user)
        resp = self._submit()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_named_survey_overrides_previous_submission(self):
        self.client.force_authenticate(user=self.user)
        first = self._submit(scale="1", choice="A", text="initial")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self._submit(scale="5", choice="B", text="updated")
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        # Only one response remains for this user.
        self.assertEqual(
            SurveyResponse.objects.filter(
                survey=self.survey, respondent=self.profile
            ).count(),
            1,
        )
        saved = SurveyResponse.objects.get(survey=self.survey, respondent=self.profile)
        self.assertEqual(
            Answer.objects.get(question=self.q1, response=saved).value,
            "5",
        )
        self.assertEqual(
            Answer.objects.get(question=self.q3, response=saved).value,
            "updated",
        )

    def test_forbidden_user_cannot_submit(self):
        self.survey.forbidden_users.add(self.profile)
        self.client.force_authenticate(user=self.user)
        resp = self._submit()
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_named_survey_allows_different_users(self):
        self.client.force_authenticate(user=self.user)
        self.assertEqual(self._submit().status_code, status.HTTP_201_CREATED)
        self.client.force_authenticate(user=self.other_user)
        self.assertEqual(self._submit().status_code, status.HTTP_201_CREATED)
        self.assertEqual(SurveyResponse.objects.count(), 2)

    def test_anonymous_survey_allows_repeated_submissions(self):
        anon = Survey.objects.create(
            title="Anon", is_anonymous=True, status=SurveyStatus.ACTIVE
        )
        q = Question.objects.create(survey=anon, text="?", type="text", order=0)
        self.client.force_authenticate(user=self.user)
        for value in ("first", "second", "third"):
            r = self.client.post(
                f"/api/surveys/{anon.id}/responses/",
                {"answers": [{"question_id": q.id, "value": value}]},
                format="json",
            )
            self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        # All three persist, all anonymous (respondent stripped by Response.save()).
        self.assertEqual(anon.responses.count(), 3)
        for r in anon.responses.all():
            self.assertIsNone(r.respondent_id)

    def test_rejects_unknown_question_id(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"/api/surveys/{self.survey.id}/responses/",
            {"answers": [{"question_id": 99999, "value": "x"}]},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_submission_past_end_date(self):
        from datetime import date, timedelta

        self.survey.end_date = date.today() - timedelta(days=2)
        self.survey.save()
        self.client.force_authenticate(user=self.user)
        resp = self._submit()
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_required_question_must_have_value(self):
        # All questions in the fixture default to required=True.
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"/api/surveys/{self.survey.id}/responses/",
            {
                "answers": [
                    {"question_id": self.q1.id, "value": ""},
                    {"question_id": self.q2.id, "value": "A"},
                    {"question_id": self.q3.id, "value": "ok"},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_optional_question_can_be_blank(self):
        self.q3.required = False
        self.q3.save()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"/api/surveys/{self.survey.id}/responses/",
            {
                "answers": [
                    {"question_id": self.q1.id, "value": "4"},
                    {"question_id": self.q2.id, "value": "A"},
                    {"question_id": self.q3.id, "value": ""},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_missing_required_answer_rejected(self):
        # Don't include q3 at all; it's required → reject.
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"/api/surveys/{self.survey.id}/responses/",
            {
                "answers": [
                    {"question_id": self.q1.id, "value": "4"},
                    {"question_id": self.q2.id, "value": "A"},
                ]
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_answers_rejected(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            f"/api/surveys/{self.survey.id}/responses/",
            {"answers": []},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
