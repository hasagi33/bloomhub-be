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
