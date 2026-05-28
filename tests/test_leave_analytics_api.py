from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import LeaveRequestStatus, LeaveType
from core.models import (
    LeaveBalance,
    LeaveMonthlyAggregate,
    LeavePolicy,
    LeaveRequest,
    Permission,
)
from core.services.leave_analytics_service import (
    materialize_leave_monthly_aggregates,
)


class LeaveAnalyticsAPITestCase(APITestCase):
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
        self.outsider = User.objects.create_user(
            username="outsider",
            email="outsider@example.com",
            password="pass123",
            first_name="Out",
            last_name="Sider",
        )

        self.employee_profile = self.employee_user.profile
        self.hr_profile = self.hr_user.profile
        self.outsider_profile = self.outsider.profile

        self.employee_profile.department = "Engineering"
        self.employee_profile.save(update_fields=["department"])

        # One approved leave for the employee in Feb 2026 (3 working days).
        LeaveRequest.objects.create(
            employee=self.employee_profile,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 2, 3),
            end_date=date(2026, 2, 5),
            reason="ski",
            status=LeaveRequestStatus.APPROVED,
        )
        # One pending sick leave for the outsider in Feb 2026 (2 days).
        LeaveRequest.objects.create(
            employee=self.outsider_profile,
            leave_type=LeaveType.SICK,
            start_date=date(2026, 2, 10),
            end_date=date(2026, 2, 11),
            reason="flu",
            status=LeaveRequestStatus.APPROVED,
        )
        materialize_leave_monthly_aggregates()

        LeavePolicy.objects.update_or_create(
            leave_type=LeaveType.VACATION,
            defaults={
                "allocated_days_per_year": 25,
                "carryover_days": 5,
                "requires_approval": True,
                "requires_covering_employee": False,
                "min_notice_in_days": 0,
                "max_consecutive_days": None,
            },
        )
        LeaveBalance.objects.update_or_create(
            employee=self.employee_profile,
            leave_type=LeaveType.VACATION,
            year=2026,
            defaults={"allocated": 25, "used": 3, "carryover": 2},
        )

    def _grant_permissions(self, user: User, actions: list[str]):
        profile = user.profile
        for feature_action in actions:
            permission, _ = Permission.objects.get_or_create(
                module_name="Vacations",
                feature_action=feature_action,
            )
            profile.add_permission(permission)

    def _auth_as(self, user: User):
        self.client.force_authenticate(user=user)

    @staticmethod
    def _extract_results(payload):
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        return payload

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get("/api/leave-analytics/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_employee_without_permission_cannot_list(self):
        self._auth_as(self.employee_user)
        response = self.client.get("/api/leave-analytics/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_employee_with_own_permission_sees_only_own_rows(self):
        self._grant_permissions(self.employee_user, ["view_own_history"])
        self._auth_as(self.employee_user)

        response = self.client.get("/api/leave-analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = self._extract_results(response.json())
        employee_ids = {row["employee_id"] for row in results}
        self.assertEqual(employee_ids, {self.employee_profile.id})

    def test_hr_with_dept_trends_sees_all_rows(self):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)

        response = self.client.get("/api/leave-analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = self._extract_results(response.json())
        employee_ids = {row["employee_id"] for row in results}
        self.assertIn(self.employee_profile.id, employee_ids)
        self.assertIn(self.outsider_profile.id, employee_ids)

    def test_monthly_action_returns_twelve_rows(self):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)

        response = self.client.get("/api/leave-analytics/monthly/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        payload = response.json()
        self.assertEqual(len(payload), 12)
        feb = next(row for row in payload if row["month"] == 2)
        self.assertEqual(feb["year"], 2026)
        self.assertEqual(feb["month_label"], "Feb")
        self.assertEqual(feb["by_type"][LeaveType.VACATION], 3)
        self.assertEqual(feb["by_type"][LeaveType.SICK], 2)
        self.assertEqual(feb["total"], 5)

    def test_yearly_totals_match_aggregates(self):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)

        response = self.client.get("/api/leave-analytics/yearly-totals/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["year"], 2026)
        self.assertEqual(payload["by_type"][LeaveType.VACATION], 3)
        self.assertEqual(payload["by_type"][LeaveType.SICK], 2)
        self.assertEqual(payload["total"], 5)
        self.assertIn("pending_total", payload)
        self.assertIn("headcount", payload)
        self.assertIn("on_leave_today", payload)
        self.assertGreaterEqual(payload["headcount"], 3)

    def test_departments_action_blocked_without_trend_permission(self):
        self._grant_permissions(self.employee_user, ["view_own_history"])
        self._auth_as(self.employee_user)
        response = self.client.get("/api/leave-analytics/departments/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_departments_action_returns_breakdown(self):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)
        response = self.client.get("/api/leave-analytics/departments/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        departments = {row["department"]: row for row in response.json()}
        self.assertIn("Engineering", departments)
        eng = departments["Engineering"]
        self.assertEqual(eng["by_type"][LeaveType.VACATION], 3)

    def test_employees_action_returns_per_person_summary(self):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)
        response = self.client.get("/api/leave-analytics/employees/?year=2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {row["employee_id"]: row for row in response.json()}
        self.assertIn(self.employee_profile.id, by_id)
        emp = by_id[self.employee_profile.id]
        self.assertEqual(emp["vacation_used"], 3)
        # allocated 25 + carryover 2 - used 3 = 24
        self.assertEqual(emp["vacation_remaining"], 24)

    def test_refresh_requires_elevated_permission(self):
        self._grant_permissions(self.employee_user, ["view_own_history"])
        self._auth_as(self.employee_user)
        response = self.client.post("/api/leave-analytics/refresh/", {})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_refresh_runs_for_privileged_user(self):
        self._grant_permissions(self.hr_user, ["configure_leave_types"])
        self._auth_as(self.hr_user)

        # Add a brand new approved request after the previous materialization.
        LeaveRequest.objects.create(
            employee=self.employee_profile,
            leave_type=LeaveType.WFH,
            start_date=date(2026, 3, 2),
            end_date=date(2026, 3, 2),
            reason="wfh",
            status=LeaveRequestStatus.APPROVED,
        )

        response = self.client.post(
            "/api/leave-analytics/refresh/",
            {"year_from": 2026, "year_to": 2026},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertIn("created_count", payload)
        self.assertIn("snapshots", payload)
        self.assertTrue(
            LeaveMonthlyAggregate.objects.filter(
                employee=self.employee_profile,
                leave_type=LeaveType.WFH,
                year=2026,
                month=3,
            ).exists()
        )

    def test_refresh_rejects_partial_year_range(self):
        self._grant_permissions(self.hr_user, ["configure_leave_types"])
        self._auth_as(self.hr_user)
        response = self.client.post(
            "/api/leave-analytics/refresh/",
            {"year_from": 2026},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
