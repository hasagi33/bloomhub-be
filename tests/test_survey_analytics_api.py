"""Tests for BHB-453 — Survey analytics aggregation endpoint."""

from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    Answer,
    Question,
    Role,
    Survey,
    UserProfile,
)
from core.models import (
    Response as SurveyResponse,
)


class SurveyAnalyticsAPITests(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.hr_role, _ = Role.objects.get_or_create(name="HR")

        self.hr_user = User.objects.create_user(
            username="hr_user",
            email="hr@test.com",
            password="pass",
            is_staff=True,
        )
        hr_profile = UserProfile.objects.get(user=self.hr_user)
        hr_profile.role = self.hr_role
        hr_profile.save()

        self.regular_user = User.objects.create_user(
            username="regular", email="r@test.com", password="pass"
        )
        self.regular_profile = UserProfile.objects.get(user=self.regular_user)
        self.regular_profile.department = "Engineering"
        self.regular_profile.save()

        self.other_user = User.objects.create_user(
            username="other", email="o@test.com", password="pass"
        )
        self.other_profile = UserProfile.objects.get(user=self.other_user)
        self.other_profile.department = "Marketing"
        self.other_profile.save()

        self.survey = Survey.objects.create(
            title="Q3 Pulse", is_anonymous=False, status="active"
        )
        self.q_scale = Question.objects.create(
            survey=self.survey, text="How happy?", type="scale", order=0
        )
        self.q_choice = Question.objects.create(
            survey=self.survey,
            text="Pick one",
            type="choice",
            order=1,
            options=["Alpha", "Beta", "Gamma"],
        )
        self.q_text = Question.objects.create(
            survey=self.survey, text="Anything else?", type="text", order=2
        )

    def _create_response(self, profile, *, scale, choice, text, day_offset=0):
        r = SurveyResponse.objects.create(survey=self.survey, respondent=profile)
        if day_offset:
            new_dt = timezone.now() - timedelta(days=day_offset)
            SurveyResponse.objects.filter(pk=r.pk).update(submitted_at=new_dt)
            r.refresh_from_db()
        Answer.objects.create(question=self.q_scale, response=r, value=str(scale))
        Answer.objects.create(question=self.q_choice, response=r, value=choice)
        Answer.objects.create(question=self.q_text, response=r, value=text)
        return r

    # ── Permission ──────────────────────────────────────────────────────────

    def test_regular_user_blocked_from_analytics(self):
        self.client.force_authenticate(user=self.regular_user)
        resp = self.client.get(f"/api/surveys/{self.survey.id}/analytics/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_blocked(self):
        resp = self.client.get(f"/api/surveys/{self.survey.id}/analytics/")
        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    # ── Aggregation shape ───────────────────────────────────────────────────

    def test_empty_survey_returns_zero_counts(self):
        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get(f"/api/surveys/{self.survey.id}/analytics/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_responses"], 0)
        self.assertEqual(len(resp.data["questions"]), 3)
        for q in resp.data["questions"]:
            self.assertEqual(q["response_count"], 0)

    def test_aggregation_basics(self):
        self._create_response(
            self.regular_profile, scale=5, choice="Alpha", text="Great"
        )
        self._create_response(self.other_profile, scale=3, choice="Beta", text="Meh")
        self._create_response(self.regular_profile, scale=4, choice="Alpha", text="")

        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get(f"/api/surveys/{self.survey.id}/analytics/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_responses"], 3)

        questions_by_id = {q["question_id"]: q for q in resp.data["questions"]}

        scale_q = questions_by_id[self.q_scale.id]
        self.assertEqual(scale_q["response_count"], 3)
        self.assertEqual(scale_q["average"], round((5 + 3 + 4) / 3, 2))

        choice_q = questions_by_id[self.q_choice.id]
        dist = {row["value"]: row["count"] for row in choice_q["distribution"]}
        self.assertEqual(dist["Alpha"], 2)
        self.assertEqual(dist["Beta"], 1)
        self.assertEqual(dist["Gamma"], 0)

        text_q = questions_by_id[self.q_text.id]
        # Three answers were recorded but one was empty string — only non-empty samples.
        self.assertIn("Great", text_q["samples"])
        self.assertIn("Meh", text_q["samples"])
        self.assertNotIn("", text_q["samples"])

    # ── Department filter ───────────────────────────────────────────────────

    def test_department_filter_narrows_results(self):
        self._create_response(self.regular_profile, scale=5, choice="Alpha", text="x")
        self._create_response(self.other_profile, scale=2, choice="Beta", text="y")
        self.client.force_authenticate(user=self.hr_user)

        resp = self.client.get(
            f"/api/surveys/{self.survey.id}/analytics/?department=Engineering"
        )
        self.assertEqual(resp.data["total_responses"], 1)
        scale_q = next(
            q for q in resp.data["questions"] if q["question_id"] == self.q_scale.id
        )
        self.assertEqual(scale_q["average"], 5.0)

    def test_department_filter_ignored_for_anonymous_surveys(self):
        # Anonymous surveys strip respondent, so the filter can't apply.
        anon = Survey.objects.create(title="Anon", is_anonymous=True)
        q = Question.objects.create(survey=anon, text="ok?", type="scale", order=0)
        r = SurveyResponse(survey=anon, respondent=self.regular_profile)
        r.save()  # respondent gets nulled by the model's save() hook
        Answer.objects.create(question=q, response=r, value="4")

        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get(
            f"/api/surveys/{anon.id}/analytics/?department=Engineering"
        )
        self.assertEqual(resp.data["total_responses"], 1)

    # ── Date filter ─────────────────────────────────────────────────────────

    def test_date_range_filter(self):
        self._create_response(
            self.regular_profile, scale=5, choice="Alpha", text="recent"
        )
        self._create_response(
            self.regular_profile, scale=2, choice="Beta", text="old", day_offset=30
        )

        self.client.force_authenticate(user=self.hr_user)
        # Use yesterday as start to avoid TZ races between Python date.today()
        # and Django's timezone.localdate() in the view.
        start = (date.today() - timedelta(days=1)).isoformat()
        recent_only = self.client.get(
            f"/api/surveys/{self.survey.id}/analytics/?start_date={start}"
        )
        self.assertEqual(recent_only.data["total_responses"], 1)

        past = (date.today() - timedelta(days=60)).isoformat()
        end = (date.today() - timedelta(days=10)).isoformat()
        old_only = self.client.get(
            f"/api/surveys/{self.survey.id}/analytics/"
            f"?start_date={past}&end_date={end}"
        )
        self.assertEqual(old_only.data["total_responses"], 1)

    # ── Trend ───────────────────────────────────────────────────────────────

    def test_responses_over_time_returned(self):
        self._create_response(
            self.regular_profile, scale=4, choice="Alpha", text="a", day_offset=2
        )
        self._create_response(
            self.regular_profile, scale=5, choice="Alpha", text="b", day_offset=2
        )
        self._create_response(self.regular_profile, scale=3, choice="Beta", text="c")

        self.client.force_authenticate(user=self.hr_user)
        resp = self.client.get(f"/api/surveys/{self.survey.id}/analytics/")
        trend = resp.data["responses_over_time"]
        self.assertEqual(len(trend), 2)
        total = sum(row["count"] for row in trend)
        self.assertEqual(total, 3)
