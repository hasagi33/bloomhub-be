from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import ReviewNoteVisibility
from core.models import PerformanceReview, PerformanceReviewHistoryEvent, Permission


class PerformanceReviewAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.employee_user = User.objects.create_user(
            username="employee-user",
            email="employee@example.com",
            password="pass123",
            first_name="Employee",
            last_name="User",
        )
        self.reviewer_user = User.objects.create_user(
            username="reviewer-user",
            email="reviewer@example.com",
            password="pass123",
            first_name="Reviewer",
            last_name="User",
        )
        self.other_user = User.objects.create_user(
            username="other-user",
            email="other@example.com",
            password="pass123",
            first_name="Other",
            last_name="User",
        )

        self.employee_profile = self.employee_user.profile
        self.reviewer_profile = self.reviewer_user.profile
        self.other_profile = self.other_user.profile

        self.review = PerformanceReview.objects.create(
            employee=self.employee_profile,
            reviewer=self.reviewer_profile,
            created_by=self.reviewer_profile,
            updated_by=self.reviewer_profile,
            review_type=PerformanceReview.ReviewType.QUARTERLY,
            title="Q3 Review",
            scheduled_date=date.today() + timedelta(days=7),
            status=PerformanceReview.Status.SCHEDULED,
            reminder_offsets_days=[3, 1],
        )

    def _grant_permissions(self, user: User, actions: list[str]):
        profile = user.profile
        for action in actions:
            permission, _ = Permission.objects.get_or_create(
                module_name="Reviews",
                feature_action=action,
            )
            profile.add_permission(permission)

    def _auth_as(self, user: User):
        self.client.force_authenticate(user=user)

    @staticmethod
    def _extract_results(payload):
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        return payload

    def test_employee_can_only_list_own_reviews(self):
        self._grant_permissions(self.employee_user, ["view_own_reviews"])

        other_review = PerformanceReview.objects.create(
            employee=self.other_profile,
            reviewer=self.reviewer_profile,
            created_by=self.reviewer_profile,
            updated_by=self.reviewer_profile,
            review_type=PerformanceReview.ReviewType.ANNUAL,
            title="Annual Review",
            scheduled_date=date.today() + timedelta(days=10),
            status=PerformanceReview.Status.SCHEDULED,
        )

        self._auth_as(self.employee_user)
        response = self.client.get("/api/performance-reviews/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = self._extract_results(response.json())
        review_ids = {item["id"] for item in results}
        self.assertIn(self.review.id, review_ids)
        self.assertNotIn(other_review.id, review_ids)

    def test_private_note_employee_cannot_edit(self):
        self._grant_permissions(
            self.reviewer_user,
            ["create_review_direct_report", "add_private_feedback"],
        )
        self._grant_permissions(
            self.employee_user, ["view_own_reviews", "initiate_self_review"]
        )

        self._auth_as(self.reviewer_user)
        create_response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/notes/",
            {"visibility": "private", "content": "Private reviewer-only note"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        note_id = create_response.data["id"]

        self._auth_as(self.employee_user)
        update_response = self.client.patch(
            f"/api/performance-reviews/{self.review.id}/notes/{note_id}/",
            {"content": "Employee attempted edit"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_shared_note_employee_can_edit(self):
        self._grant_permissions(
            self.reviewer_user,
            ["create_review_direct_report", "add_shared_feedback"],
        )
        self._grant_permissions(
            self.employee_user, ["view_own_reviews", "initiate_self_review"]
        )

        self._auth_as(self.reviewer_user)
        create_response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/notes/",
            {"visibility": "shared", "content": "Shared note"},
            format="json",
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        note_id = create_response.data["id"]

        self._auth_as(self.employee_user)
        update_response = self.client.patch(
            f"/api/performance-reviews/{self.review.id}/notes/{note_id}/",
            {"content": "Updated by employee"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.data["content"], "Updated by employee")

    def test_employee_can_view_shared_review_notes_and_action_points(self):
        self._grant_permissions(
            self.reviewer_user,
            [
                "create_review_direct_report",
                "add_shared_feedback",
                "add_private_feedback",
            ],
        )

        self._auth_as(self.reviewer_user)
        shared_note_response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/notes/",
            {"visibility": "shared", "content": "Shared note"},
            format="json",
        )
        self.assertEqual(shared_note_response.status_code, status.HTTP_201_CREATED)

        private_note_response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/notes/",
            {"visibility": "private", "content": "Private note"},
            format="json",
        )
        self.assertEqual(private_note_response.status_code, status.HTTP_201_CREATED)

        action_point_response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/action-points/",
            {
                "title": "Improve onboarding",
                "description": "Complete the onboarding checklist",
            },
            format="json",
        )
        self.assertEqual(action_point_response.status_code, status.HTTP_201_CREATED)

        self._auth_as(self.employee_user)
        notes_response = self.client.get(
            f"/api/performance-reviews/{self.review.id}/notes/"
        )
        self.assertEqual(notes_response.status_code, status.HTTP_200_OK)
        notes = notes_response.json()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["visibility"], ReviewNoteVisibility.SHARED)
        self.assertEqual(notes[0]["content"], "Shared note")

        action_points_response = self.client.get(
            f"/api/performance-reviews/{self.review.id}/action-points/"
        )
        self.assertEqual(action_points_response.status_code, status.HTTP_200_OK)
        action_points = action_points_response.json()
        self.assertEqual(len(action_points), 1)
        self.assertEqual(action_points[0]["title"], "Improve onboarding")

    def test_employee_can_view_review_attachments(self):
        self._grant_permissions(
            self.reviewer_user,
            ["create_review_direct_report", "attach_documents"],
        )

        self._auth_as(self.reviewer_user)
        upload_response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/attachments/",
            {
                "file": SimpleUploadedFile(
                    "notes.txt",
                    b"review attachment",
                    content_type="text/plain",
                ),
                "description": "shared attachment",
            },
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, status.HTTP_201_CREATED)

        self._auth_as(self.employee_user)
        attachments_response = self.client.get(
            f"/api/performance-reviews/{self.review.id}/attachments/"
        )
        self.assertEqual(attachments_response.status_code, status.HTTP_200_OK)
        attachments = attachments_response.json()
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["description"], "shared attachment")

    def test_status_transition_creates_history_and_completed_at(self):
        self._grant_permissions(self.reviewer_user, ["create_review_direct_report"])

        self._auth_as(self.reviewer_user)
        response = self.client.post(
            f"/api/performance-reviews/{self.review.id}/status/",
            {"status": "completed", "comment": "Review completed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["completed_at"])

        status_events = PerformanceReviewHistoryEvent.objects.filter(
            review=self.review,
            event_type=PerformanceReviewHistoryEvent.EventType.STATUS_CHANGED,
        )
        self.assertTrue(status_events.exists())

    def test_attachment_upload_requires_attach_documents_permission(self):
        self._grant_permissions(self.employee_user, ["view_own_reviews"])

        self._auth_as(self.employee_user)
        forbidden_upload = self.client.post(
            f"/api/performance-reviews/{self.review.id}/attachments/",
            {
                "file": SimpleUploadedFile(
                    "notes.txt",
                    b"review attachment",
                    content_type="text/plain",
                ),
                "description": "test attachment",
            },
            format="multipart",
        )
        self.assertEqual(forbidden_upload.status_code, status.HTTP_403_FORBIDDEN)

        self._grant_permissions(self.employee_user, ["attach_documents"])
        allowed_upload = self.client.post(
            f"/api/performance-reviews/{self.review.id}/attachments/",
            {
                "file": SimpleUploadedFile(
                    "notes.txt",
                    b"review attachment",
                    content_type="text/plain",
                ),
                "description": "test attachment",
            },
            format="multipart",
        )
        self.assertEqual(allowed_upload.status_code, status.HTTP_201_CREATED)

    def test_reminders_auto_materialize_and_mark_read(self):
        self._grant_permissions(self.employee_user, ["view_own_reviews"])

        self.review.scheduled_date = date.today() - timedelta(days=1)
        self.review.status = PerformanceReview.Status.SCHEDULED
        self.review.save(update_fields=["scheduled_date", "status"])

        self._auth_as(self.employee_user)
        reminders_response = self.client.get(
            "/api/performance-review-reminders/?is_read=false"
        )
        self.assertEqual(reminders_response.status_code, status.HTTP_200_OK)
        reminders = self._extract_results(reminders_response.json())
        self.assertGreater(len(reminders), 0)

        reminder_id = reminders[0]["id"]
        mark_read_response = self.client.post(
            f"/api/performance-review-reminders/{reminder_id}/mark-read/"
        )
        self.assertEqual(mark_read_response.status_code, status.HTTP_200_OK)
        self.assertTrue(mark_read_response.data["is_read"])

        read_event = PerformanceReviewHistoryEvent.objects.filter(
            review=self.review,
            event_type=PerformanceReviewHistoryEvent.EventType.REMINDER_READ,
        ).first()
        self.assertIsNotNone(read_event)
