from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import LeaveRequestStatus, LeaveType
from core.models import LeaveRequest, Permission
from core.services.leave_analytics_export_service import (
    CSV_CONTENT_TYPE,
    PDF_CONTENT_TYPE,
)
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
)


class LeaveAnalyticsExportAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.employee_user = User.objects.create_user(
            username="emp-user",
            email="emp@example.com",
            password="pass123",
            first_name="Emp",
            last_name="User",
        )
        self.hr_user = User.objects.create_user(
            username="hr-user",
            email="hr@example.com",
            password="pass123",
            first_name="HR",
            last_name="Admin",
        )
        self.other_user = User.objects.create_user(
            username="other-user",
            email="other@example.com",
            password="pass123",
            first_name="Other",
            last_name="Person",
        )

        self.employee_profile = self.employee_user.profile
        self.other_profile = self.other_user.profile
        self.employee_profile.department = "Engineering"
        self.other_profile.department = "Design"
        self.employee_profile.save(update_fields=["department"])
        self.other_profile.save(update_fields=["department"])

        LeaveRequest.objects.create(
            employee=self.employee_profile,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 2, 3),
            end_date=date(2026, 2, 5),
            reason="ski",
            status=LeaveRequestStatus.APPROVED,
        )
        LeaveRequest.objects.create(
            employee=self.other_profile,
            leave_type=LeaveType.SICK,
            start_date=date(2026, 2, 10),
            end_date=date(2026, 2, 11),
            reason="flu",
            status=LeaveRequestStatus.APPROVED,
        )
        materialize_leave_monthly_aggregates()

    def _grant_permissions(self, user: User, actions: list[str]):
        for feature_action in actions:
            permission, _ = Permission.objects.get_or_create(
                module_name="Vacations",
                feature_action=feature_action,
            )
            user.profile.add_permission(permission)

    def _auth_as(self, user: User):
        self.client.force_authenticate(user=user)

    def test_export_rejects_unknown_format(self):
        self._auth_as(self.hr_user)
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        response = self.client.get(
            "/api/leave-analytics/export/", {"format": "txt", "year": 2026}
        )
        # DRF content negotiation rejects unknown formats with 404 before the
        # view runs; either way the client must not receive a 200.
        self.assertIn(
            response.status_code,
            {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND},
        )

    def test_export_csv_for_hr_includes_all_employees(self):
        self._auth_as(self.hr_user)
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        response = self.client.get(
            "/api/leave-analytics/export/", {"format": "csv", "year": 2026}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], CSV_CONTENT_TYPE)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".csv", response["Content-Disposition"])
        body = response.content.decode("utf-8-sig")
        self.assertIn("employee_id", body)
        self.assertIn("Emp User", body)
        self.assertIn("Other Person", body)

    def test_export_csv_for_employee_scopes_to_own_rows(self):
        self._auth_as(self.employee_user)
        self._grant_permissions(self.employee_user, ["view_own_history"])
        response = self.client.get(
            "/api/leave-analytics/export/", {"format": "csv", "year": 2026}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.content.decode("utf-8-sig")
        self.assertIn("Emp User", body)
        self.assertNotIn("Other Person", body)

    def test_export_csv_filters_by_department_for_hr(self):
        self._auth_as(self.hr_user)
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        response = self.client.get(
            "/api/leave-analytics/export/",
            {"format": "csv", "year": 2026, "department": "Design"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.content.decode("utf-8-sig")
        self.assertNotIn("Emp User", body)
        self.assertIn("Other Person", body)
        self.assertIn("design", response["Content-Disposition"].lower())

    def test_export_csv_ignores_department_for_non_hr(self):
        self._auth_as(self.employee_user)
        self._grant_permissions(self.employee_user, ["view_own_history"])
        response = self.client.get(
            "/api/leave-analytics/export/",
            {"format": "csv", "year": 2026, "department": "Design"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.content.decode("utf-8-sig")
        self.assertIn("Emp User", body)
        self.assertNotIn("Other Person", body)

    def test_export_pdf_returns_pdf_bytes(self):
        self._auth_as(self.hr_user)
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        response = self.client.get(
            "/api/leave-analytics/export/", {"format": "pdf", "year": 2026}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], PDF_CONTENT_TYPE)
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertIn(".pdf", response["Content-Disposition"])

    def test_export_month_filter_narrows_csv_rows(self):
        LeaveRequest.objects.create(
            employee=self.employee_profile,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 7, 6),
            end_date=date(2026, 7, 6),
            reason="single",
            status=LeaveRequestStatus.APPROVED,
        )
        materialize_leave_monthly_aggregates()

        self._auth_as(self.hr_user)
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        response = self.client.get(
            "/api/leave-analytics/export/",
            {"format": "csv", "year": 2026, "month": 7},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.content.decode("utf-8-sig")
        non_header_lines = [line for line in body.splitlines()[1:] if line.strip()]
        self.assertEqual(len(non_header_lines), 1)
        self.assertIn(",7,", non_header_lines[0])
