from datetime import date

from django.contrib.auth.models import User
from django.core.management import call_command
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import (
    LeaveRequestStatus,
    LeaveType,
    ProjectAssignmentStatus,
    ProjectStatus,
    ProjectType,
)
from core.models import (
    LeaveRequest,
    Permission,
    Project,
    ProjectAssignment,
)

AVAILABILITY_URL = "/api/leave-analytics/availability/"


class LeaveAvailabilityAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.hr_user = User.objects.create_user(
            username="hr-user",
            email="hr@example.com",
            password="pass123",
            first_name="HR",
            last_name="Admin",
        )
        self.emp_a_user = User.objects.create_user(
            username="emp-a",
            email="a@example.com",
            password="pass123",
            first_name="Aida",
            last_name="A",
        )
        self.emp_b_user = User.objects.create_user(
            username="emp-b",
            email="b@example.com",
            password="pass123",
            first_name="Bora",
            last_name="B",
        )
        self.emp_c_user = User.objects.create_user(
            username="emp-c",
            email="c@example.com",
            password="pass123",
            first_name="Cleo",
            last_name="C",
        )
        self.outsider_user = User.objects.create_user(
            username="outsider",
            email="out@example.com",
            password="pass123",
            first_name="Out",
            last_name="Sider",
        )

        self.hr = self.hr_user.profile
        self.emp_a = self.emp_a_user.profile
        self.emp_b = self.emp_b_user.profile
        self.emp_c = self.emp_c_user.profile
        self.outsider = self.outsider_user.profile

        self.project = Project.objects.create(
            name="Acme Web",
            client="Acme",
            project_type=ProjectType.CLIENT,
            status=ProjectStatus.PLANNED,
        )
        for profile in (self.emp_a, self.emp_b, self.emp_c):
            ProjectAssignment.objects.create(
                user_profile=profile,
                project=self.project,
                start_date=date(2026, 1, 1),
                status=ProjectAssignmentStatus.ACTIVE,
            )

        # Mon-Fri vacation overlapping the window for emp_a.
        # Window we'll use: 2026-05-04 (Mon) -> 2026-05-08 (Fri), 5 working days.
        LeaveRequest.objects.create(
            employee=self.emp_a,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 6),
            reason="trip",
            status=LeaveRequestStatus.APPROVED,
        )
        # emp_b sick same Tue+Wed -> with emp_a Tue + Wed = 2/3 = 66% critical.
        LeaveRequest.objects.create(
            employee=self.emp_b,
            leave_type=LeaveType.SICK,
            start_date=date(2026, 5, 5),
            end_date=date(2026, 5, 6),
            reason="flu",
            status=LeaveRequestStatus.APPROVED,
        )
        # emp_c WFH inside window — should NOT contribute by default.
        LeaveRequest.objects.create(
            employee=self.emp_c,
            leave_type=LeaveType.WFH,
            start_date=date(2026, 5, 7),
            end_date=date(2026, 5, 8),
            reason="wfh",
            status=LeaveRequestStatus.APPROVED,
        )
        # Outsider has a leave but is not on the project — must be filtered.
        LeaveRequest.objects.create(
            employee=self.outsider,
            leave_type=LeaveType.VACATION,
            start_date=date(2026, 5, 5),
            end_date=date(2026, 5, 6),
            reason="off",
            status=LeaveRequestStatus.APPROVED,
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

    def _hr_query(self, **params):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"{AVAILABILITY_URL}?{query}")

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(AVAILABILITY_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_missing_start_date_returns_400(self):
        self._grant_permissions(self.hr_user, ["view_dept_trends"])
        self._auth_as(self.hr_user)
        response = self.client.get(f"{AVAILABILITY_URL}?end_date=2026-05-08")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_window_exceeding_cap_returns_400(self):
        response = self._hr_query(start_date="2026-01-01", end_date="2026-03-31")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_end_before_start_returns_400(self):
        response = self._hr_query(start_date="2026-05-08", end_date="2026-05-04")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_project_scope_filters_to_assignees(self):
        response = self._hr_query(
            start_date="2026-05-04",
            end_date="2026-05-08",
            project=self.project.id,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        ids = {row["employee_id"] for row in payload["employees"]}
        self.assertEqual(ids, {self.emp_a.id, self.emp_b.id, self.emp_c.id})
        self.assertEqual(payload["range"]["project_id"], self.project.id)
        self.assertEqual(payload["range"]["headcount"], 3)
        self.assertEqual(payload["range"]["working_days_count"], 5)

    def test_wfh_excluded_by_default(self):
        response = self._hr_query(
            start_date="2026-05-04",
            end_date="2026-05-08",
            project=self.project.id,
        )
        payload = response.json()
        emp_c_row = next(
            r for r in payload["employees"] if r["employee_id"] == self.emp_c.id
        )
        self.assertEqual(emp_c_row["entries"], [])

    def test_critical_day_flag_triggers_at_30_percent(self):
        response = self._hr_query(
            start_date="2026-05-04",
            end_date="2026-05-08",
            project=self.project.id,
        )
        payload = response.json()
        by_date = {row["date"]: row for row in payload["daily"]}
        # Tue 2026-05-05: emp_a vacation + emp_b sick = 2/3 = 67% -> critical.
        self.assertTrue(by_date["2026-05-05"]["is_critical"])
        self.assertEqual(by_date["2026-05-05"]["on_leave_count"], 2)
        # Thu 2026-05-07: nobody (WFH excluded) -> not critical.
        self.assertFalse(by_date["2026-05-07"]["is_critical"])
        self.assertEqual(by_date["2026-05-07"]["on_leave_count"], 0)

    def test_explicit_leave_type_filter_includes_wfh(self):
        response = self._hr_query(
            start_date="2026-05-04",
            end_date="2026-05-08",
            project=self.project.id,
            leave_type=LeaveType.WFH,
        )
        payload = response.json()
        emp_c_row = next(
            r for r in payload["employees"] if r["employee_id"] == self.emp_c.id
        )
        self.assertEqual(len(emp_c_row["entries"]), 1)
        self.assertEqual(emp_c_row["entries"][0]["leave_type"], LeaveType.WFH)

    def test_status_filter_restricts_to_approved_only(self):
        # Add a pending request for emp_c — default would include it, but
        # restricting to approved must drop it.
        LeaveRequest.objects.create(
            employee=self.emp_c,
            leave_type=LeaveType.PERSONAL,
            start_date=date(2026, 5, 4),
            end_date=date(2026, 5, 4),
            reason="errand",
            status=LeaveRequestStatus.PENDING,
        )
        response = self._hr_query(
            start_date="2026-05-04",
            end_date="2026-05-08",
            project=self.project.id,
            status=LeaveRequestStatus.APPROVED,
        )
        payload = response.json()
        emp_c_row = next(
            r for r in payload["employees"] if r["employee_id"] == self.emp_c.id
        )
        # WFH still excluded by default leave-type filter, pending dropped.
        self.assertEqual(emp_c_row["entries"], [])

    def test_unassigned_user_without_org_perm_gets_403(self):
        self._grant_permissions(self.outsider_user, ["view_own_history"])
        self._auth_as(self.outsider_user)
        response = self.client.get(
            f"{AVAILABILITY_URL}?start_date=2026-05-04&end_date=2026-05-08"
            f"&project={self.project.id}"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_own_data_fallback_returns_only_caller(self):
        self._grant_permissions(self.emp_a_user, ["view_own_history"])
        self._auth_as(self.emp_a_user)
        response = self.client.get(
            f"{AVAILABILITY_URL}?start_date=2026-05-04&end_date=2026-05-08"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        ids = {row["employee_id"] for row in payload["employees"]}
        self.assertEqual(ids, {self.emp_a.id})
