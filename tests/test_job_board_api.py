"""Tests for the internal job board API (BHB-462)."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    Application,
    ApplicationStatus,
    Department,
    JobListing,
    JobListingStatus,
    Notification,
    UserProfile,
)


def _extract_results(payload):
    """Unwrap DRF's paginated envelope when present."""
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload


class JobBoardAPITestCase(APITestCase):
    """End-to-end coverage for ``/api/job-listings/``."""

    def setUp(self):
        self.engineering = Department.objects.create(name="Engineering")
        self.design = Department.objects.create(name="Design")

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

        now = timezone.now()
        self.active_eng = JobListing.objects.create(
            title="Senior Backend Engineer",
            description="Own the search platform.",
            department=self.engineering,
            open_at=now - timedelta(days=2),
            close_at=now + timedelta(days=14),
            status=JobListingStatus.OPEN,
        )
        self.active_design = JobListing.objects.create(
            title="Product Designer",
            description="Design the activation surfaces.",
            department=self.design,
            open_at=now - timedelta(days=1),
            close_at=now + timedelta(days=10),
            status=JobListingStatus.OPEN,
        )
        # Inactive: draft
        self.draft = JobListing.objects.create(
            title="Draft Role",
            department=self.engineering,
            open_at=now - timedelta(days=1),
            close_at=now + timedelta(days=10),
            status=JobListingStatus.DRAFT,
        )
        # Inactive: closed (status)
        self.closed = JobListing.objects.create(
            title="Closed Role",
            department=self.engineering,
            open_at=now - timedelta(days=30),
            close_at=now + timedelta(days=10),
            status=JobListingStatus.CLOSED,
        )
        # Inactive: past close_at even though status=open
        self.expired = JobListing.objects.create(
            title="Expired Role",
            department=self.engineering,
            open_at=now - timedelta(days=30),
            close_at=now - timedelta(days=1),
            status=JobListingStatus.OPEN,
        )
        # Inactive: open_at in the future
        self.upcoming = JobListing.objects.create(
            title="Upcoming Role",
            department=self.engineering,
            open_at=now + timedelta(days=2),
            close_at=now + timedelta(days=30),
            status=JobListingStatus.OPEN,
        )

    # ── list / filter / search ──

    def test_requires_authentication(self):
        response = self.client.get("/api/job-listings/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_only_active_listings(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/job-listings/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        ids = {r["id"] for r in results}
        self.assertEqual(ids, {self.active_eng.id, self.active_design.id})

    def test_filter_by_department(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/job-listings/?department={self.design.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.active_design.id)

    def test_search_by_title(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/job-listings/?search=Backend")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.active_eng.id)

    def test_search_by_description_keyword(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/job-listings/?search=activation")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.active_design.id)

    def test_retrieve_active_listing(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/job-listings/{self.active_eng.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["id"], self.active_eng.id)
        self.assertEqual(data["department_name"], "Engineering")
        self.assertIn("description", data)
        self.assertFalse(data["has_applied"])

    def test_retrieve_inactive_listing_returns_404(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/job-listings/{self.draft.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── apply ──

    def test_apply_to_listing(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            f"/api/job-listings/{self.active_eng.id}/apply/",
            {"cover_note": "Excited to contribute."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.json()
        self.assertEqual(data["listing_id"], self.active_eng.id)
        self.assertEqual(data["applicant_id"], self.emp.id)
        self.assertEqual(data["status"], ApplicationStatus.SUBMITTED)
        self.assertEqual(data["cover_note"], "Excited to contribute.")
        self.assertTrue(
            Application.objects.filter(
                listing=self.active_eng, applicant=self.emp
            ).exists()
        )

    def test_apply_twice_returns_400(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            f"/api/job-listings/{self.active_eng.id}/apply/",
            {"cover_note": "Again."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_to_inactive_listing_returns_404(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            f"/api/job-listings/{self.draft.id}/apply/",
            {"cover_note": ""},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_has_applied_flag_reflects_state(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/job-listings/{self.active_eng.id}/")
        self.assertTrue(response.json()["has_applied"])

        # A different user should not see has_applied=True for the same listing.
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(f"/api/job-listings/{self.active_eng.id}/")
        self.assertFalse(response.json()["has_applied"])

    # ── my-applications ──

    def test_my_applications_returns_only_caller_applications(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        Application.objects.create(listing=self.active_design, applicant=self.other)
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/job-listings/my-applications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["listing_id"], self.active_eng.id)
        self.assertEqual(results[0]["applicant_id"], self.emp.id)

    # ── HR listing access ──

    def test_hr_sees_all_listings_including_inactive(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/job-listings/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        ids = {r["id"] for r in results}
        self.assertEqual(
            ids,
            {
                self.active_eng.id,
                self.active_design.id,
                self.draft.id,
                self.closed.id,
                self.expired.id,
                self.upcoming.id,
            },
        )

    def test_hr_can_retrieve_inactive_listing(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(f"/api/job-listings/{self.draft.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["id"], self.draft.id)

    def test_employee_cannot_edit_listing(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.patch(
            f"/api/job-listings/{self.active_eng.id}/",
            {"title": "Principal Backend Engineer"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.active_eng.refresh_from_db()
        self.assertEqual(self.active_eng.title, "Senior Backend Engineer")

    def test_hr_can_edit_open_listing(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.patch(
            f"/api/job-listings/{self.active_eng.id}/",
            {
                "title": "Principal Backend Engineer",
                "description": "Own platform strategy.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.active_eng.refresh_from_db()
        self.assertEqual(self.active_eng.title, "Principal Backend Engineer")
        self.assertEqual(self.active_eng.description, "Own platform strategy.")

    def test_hr_can_publish_draft_listing(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.patch(
            f"/api/job-listings/{self.draft.id}/",
            {"status": JobListingStatus.OPEN},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, JobListingStatus.OPEN)

    def test_employee_cannot_delete_listing(self):
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.delete(f"/api/job-listings/{self.active_eng.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(JobListing.objects.filter(id=self.active_eng.id).exists())

    def test_hr_can_delete_open_listing(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.delete(f"/api/job-listings/{self.active_eng.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(JobListing.objects.filter(id=self.active_eng.id).exists())

    # ── applications-per-listing ──

    def test_applications_list_requires_hr(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(
            f"/api/job-listings/{self.active_eng.id}/applications/"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_applications_list_scoped_to_listing(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        Application.objects.create(listing=self.active_design, applicant=self.other)
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get(
            f"/api/job-listings/{self.active_eng.id}/applications/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["listing_id"], self.active_eng.id)
        self.assertEqual(results[0]["applicant_id"], self.emp.id)

    # ── PATCH application status (BHB-465) ──

    def _create_application(self):
        return Application.objects.create(
            listing=self.active_eng,
            applicant=self.emp,
            cover_note="initial",
        )

    def test_patch_status_requires_hr(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.patch(
            f"/api/job-applications/{app.id}/",
            {"status": ApplicationStatus.UNDER_REVIEW},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.SUBMITTED)

    def test_patch_status_succeeds_as_hr(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.hr_user)
        # SUBMITTED → UNDER_REVIEW is a legal first transition.
        response = self.client.patch(
            f"/api/job-applications/{app.id}/",
            {"status": ApplicationStatus.UNDER_REVIEW},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # Response uses the full ApplicationSerializer, not the status-only write.
        self.assertEqual(data["id"], app.id)
        self.assertEqual(data["status"], ApplicationStatus.UNDER_REVIEW)
        self.assertEqual(data["listing_id"], self.active_eng.id)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.UNDER_REVIEW)

    def test_patch_status_rejects_invalid_value(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.patch(
            f"/api/job-applications/{app.id}/",
            {"status": "not_a_real_status"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.SUBMITTED)

    def test_retrieve_application_as_applicant(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/job-applications/{app.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["id"], app.id)

    def test_retrieve_application_blocked_for_stranger(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.other_user)
        response = self.client.get(f"/api/job-applications/{app.id}/")
        # Queryset is scoped to the caller's own applications, so strangers
        # see a 404 rather than a 403.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── list applications (cross-listing, HR scope) ──

    def test_application_list_requires_authentication(self):
        response = self.client.get("/api/job-applications/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_application_list_employee_sees_only_own(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        Application.objects.create(listing=self.active_design, applicant=self.other)
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/job-applications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = _extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["applicant_id"], self.emp.id)

    def test_application_list_hr_sees_all_and_filter_by_listing(self):
        Application.objects.create(listing=self.active_eng, applicant=self.emp)
        Application.objects.create(listing=self.active_design, applicant=self.other)
        self.client.force_authenticate(user=self.hr_user)
        all_response = self.client.get("/api/job-applications/")
        self.assertEqual(all_response.status_code, status.HTTP_200_OK)
        all_results = _extract_results(all_response.json())
        self.assertEqual(len(all_results), 2)

        filtered = self.client.get(
            f"/api/job-applications/?listing={self.active_eng.id}"
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        filtered_results = _extract_results(filtered.json())
        self.assertEqual(len(filtered_results), 1)
        self.assertEqual(filtered_results[0]["listing_id"], self.active_eng.id)

    # ── apply guard hardening (BHB-465) ──

    def test_apply_to_draft_listing_returns_404_even_for_hr(self):
        self.client.force_authenticate(user=self.hr_user)
        response = self.client.post(
            f"/api/job-listings/{self.draft.id}/apply/",
            {"cover_note": "should be blocked"},
            format="json",
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND),
        )
        self.assertFalse(
            Application.objects.filter(
                listing=self.draft, applicant=self.hr_user.profile
            ).exists()
        )

    # ── workflow / state machine (BHB-465 full workflow) ──

    def _advance(self, app, new_status, *, actor=None, note=""):
        actor = actor or self.hr_user
        self.client.force_authenticate(user=actor)
        payload = {"status": new_status}
        if note:
            payload["decision_note"] = note
        return self.client.patch(
            f"/api/job-applications/{app.id}/", payload, format="json"
        )

    def test_legal_transition_submitted_to_under_review(self):
        app = self._create_application()
        response = self._advance(app, ApplicationStatus.UNDER_REVIEW)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.UNDER_REVIEW)
        # Non-terminal moves leave decision_* untouched.
        self.assertEqual(app.decision_note, "")
        self.assertIsNone(app.decided_by)
        self.assertIsNone(app.decided_at)

    def test_illegal_transition_submitted_to_shortlisted_rejected(self):
        app = self._create_application()
        response = self._advance(app, ApplicationStatus.SHORTLISTED)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.SUBMITTED)

    def test_idempotent_transition_rejected(self):
        app = self._create_application()
        response = self._advance(app, ApplicationStatus.SUBMITTED)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_captures_decision_metadata_and_notifies(self):
        app = self._create_application()
        self._advance(app, ApplicationStatus.UNDER_REVIEW)
        response = self._advance(
            app, ApplicationStatus.REJECTED, note="Not enough scope yet."
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["status"], ApplicationStatus.REJECTED)
        self.assertEqual(data["decision_note"], "Not enough scope yet.")
        self.assertEqual(data["decided_by_id"], self.hr_user.profile.id)
        self.assertIsNotNone(data["decided_at"])
        # Notification on applicant's bell.
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.emp,
                metadata__application_id=app.id,
                metadata__status=ApplicationStatus.REJECTED,
            ).exists()
        )

    def test_accept_full_happy_path(self):
        app = self._create_application()
        self._advance(app, ApplicationStatus.UNDER_REVIEW)
        self._advance(app, ApplicationStatus.SHORTLISTED)
        response = self._advance(
            app, ApplicationStatus.ACCEPTED, note="Offer extended."
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.ACCEPTED)
        self.assertEqual(app.decision_note, "Offer extended.")
        self.assertEqual(app.decided_by_id, self.hr_user.profile.id)
        self.assertIsNotNone(app.decided_at)

    def test_terminal_state_blocks_further_transitions(self):
        app = self._create_application()
        self._advance(app, ApplicationStatus.UNDER_REVIEW)
        self._advance(app, ApplicationStatus.REJECTED)
        response = self._advance(app, ApplicationStatus.UNDER_REVIEW)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hr_cannot_set_withdrawn_via_patch(self):
        app = self._create_application()
        response = self._advance(app, ApplicationStatus.WITHDRAWN)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── applicant withdraw ──

    def test_applicant_can_withdraw_their_own_application(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            f"/api/job-applications/{app.id}/withdraw/",
            {"decision_note": "Changed my mind."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.WITHDRAWN)
        self.assertEqual(app.decision_note, "Changed my mind.")
        self.assertEqual(app.decided_by_id, self.emp.id)

    def test_withdraw_blocked_for_non_applicant(self):
        app = self._create_application()
        self.client.force_authenticate(user=self.other_user)
        response = self.client.post(
            f"/api/job-applications/{app.id}/withdraw/", {}, format="json"
        )
        # Non-applicant strangers fall outside the queryset.
        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
        )

    def test_withdraw_blocked_after_terminal_state(self):
        app = self._create_application()
        self._advance(app, ApplicationStatus.UNDER_REVIEW)
        self._advance(app, ApplicationStatus.REJECTED)
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.post(
            f"/api/job-applications/{app.id}/withdraw/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── reviewer widening (listing creator) ──

    def test_listing_creator_can_review_without_being_hr(self):
        creator_user = User.objects.create_user(
            username="creator", email="creator@test.com", password="pw"
        )
        creator = UserProfile.objects.get(user=creator_user)
        self.active_eng.created_by = creator
        self.active_eng.save(update_fields=["created_by"])
        app = self._create_application()
        self.client.force_authenticate(user=creator_user)
        response = self.client.patch(
            f"/api/job-applications/{app.id}/",
            {"status": ApplicationStatus.UNDER_REVIEW},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.UNDER_REVIEW)
