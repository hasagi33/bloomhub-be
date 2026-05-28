from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.enums import (
    ProjectAssignmentStatus,
    ProjectStatus,
    ProjectType,
)
from core.models import (
    Project,
    ProjectAssignment,
    Role,
)
from core.services.time_tracking_service import weekly_allocation_summary


class ProjectsAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    # ──────────────────────────────────────
    # Setup helpers
    # ──────────────────────────────────────

    def setUp(self):
        self.admin_role = Role.objects.create(name="admin")
        self.hr_role = Role.objects.create(name="hr")
        self.manager_role = Role.objects.create(name="manager")
        self.employee_role = Role.objects.create(name="employee")

        self.admin_user = self._make_user("admin_u", self.admin_role)
        self.hr_user = self._make_user("hr_u", self.hr_role)
        self.manager_user = self._make_user("mgr_u", self.manager_role)
        self.employee_user = self._make_user("emp_u", self.employee_role)
        self.outsider_user = self._make_user("out_u", self.employee_role)

        # Projects
        self.alpha = Project.objects.create(
            name="Alpha",
            client="Acme",
            project_type=ProjectType.CLIENT,
            status=ProjectStatus.ACTIVE,
            owner=self.manager_user.profile,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.beta = Project.objects.create(
            name="Beta",
            client="Globex",
            project_type=ProjectType.CLIENT,
            status=ProjectStatus.PLANNED,
            start_date=date(2026, 6, 1),
            end_date=date(2027, 6, 1),
        )
        self.gamma = Project.objects.create(
            name="Gamma Internal",
            client=None,
            project_type=ProjectType.INTERNAL,
            status=ProjectStatus.ACTIVE,
        )

        # Manager owns alpha; assigned to nothing extra.
        # Employee assigned to alpha (active) and beta (completed).
        ProjectAssignment.objects.create(
            user_profile=self.employee_user.profile,
            project=self.alpha,
            start_date=date(2026, 1, 1),
            status=ProjectAssignmentStatus.ACTIVE,
        )
        ProjectAssignment.objects.create(
            user_profile=self.employee_user.profile,
            project=self.beta,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 9, 1),
            status=ProjectAssignmentStatus.COMPLETED,
        )

    def _make_user(self, username, role):
        user = User.objects.create_user(
            username=username, email=f"{username}@test.com", password="pass"
        )
        profile = user.profile
        profile.role = role
        profile.full_name = username
        profile.save(update_fields=["role", "full_name"])
        return user

    def _auth(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    # ──────────────────────────────────────
    # Auth
    # ──────────────────────────────────────

    def test_list_requires_authentication(self):
        self.client.credentials()
        response = self.client.get(reverse("core:project_list"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ──────────────────────────────────────
    # List
    # ──────────────────────────────────────

    def test_admin_sees_all_projects(self):
        self._auth(self.admin_user)
        response = self.client.get(reverse("core:project_list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        names = {p["name"] for p in response.data["results"]}
        self.assertEqual(names, {"Alpha", "Beta", "Gamma Internal"})

    def test_list_returns_assignment_summary(self):
        self._auth(self.hr_user)
        response = self.client.get(reverse("core:project_list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        alpha = next(p for p in response.data["results"] if p["name"] == "Alpha")
        self.assertEqual(alpha["assignment_summary"]["total_assignments"], 1)
        self.assertEqual(alpha["assignment_summary"]["active_assignments"], 1)
        self.assertEqual(alpha["assignment_summary"]["active_members"], 1)

    def test_employee_sees_only_assigned_projects(self):
        self._auth(self.employee_user)
        response = self.client.get(reverse("core:project_list"))
        names = {p["name"] for p in response.data["results"]}
        # Beta assignment is completed but employee remains visible via assignment.
        self.assertEqual(names, {"Alpha", "Beta"})

    def test_outsider_employee_sees_nothing(self):
        self._auth(self.outsider_user)
        response = self.client.get(reverse("core:project_list"))
        self.assertEqual(response.data["results"], [])

    def test_manager_sees_owned_and_assigned(self):
        ProjectAssignment.objects.create(
            user_profile=self.manager_user.profile,
            project=self.beta,
            start_date=date(2026, 6, 1),
            status=ProjectAssignmentStatus.ACTIVE,
        )
        self._auth(self.manager_user)
        response = self.client.get(reverse("core:project_list"))
        names = {p["name"] for p in response.data["results"]}
        self.assertEqual(names, {"Alpha", "Beta"})

    def test_search_matches_name_and_client(self):
        self._auth(self.admin_user)
        by_name = self.client.get(reverse("core:project_list"), {"search": "alpha"})
        by_client = self.client.get(reverse("core:project_list"), {"search": "globex"})
        self.assertEqual({p["name"] for p in by_name.data["results"]}, {"Alpha"})
        self.assertEqual({p["name"] for p in by_client.data["results"]}, {"Beta"})

    def test_filter_by_status(self):
        self._auth(self.admin_user)
        response = self.client.get(
            reverse("core:project_list"), {"status": ProjectStatus.PLANNED}
        )
        self.assertEqual({p["name"] for p in response.data["results"]}, {"Beta"})

    def test_filter_by_owner(self):
        self._auth(self.admin_user)
        response = self.client.get(
            reverse("core:project_list"),
            {"owner": self.manager_user.profile.id},
        )
        self.assertEqual({p["name"] for p in response.data["results"]}, {"Alpha"})

    def test_filter_active_date_range(self):
        self._auth(self.admin_user)
        response = self.client.get(
            reverse("core:project_list"),
            {"active_from": "2026-01-01", "active_to": "2026-03-31"},
        )
        names = {p["name"] for p in response.data["results"]}
        # Alpha (Jan-Dec) and Gamma (open dates) overlap; Beta starts in June.
        self.assertIn("Alpha", names)
        self.assertNotIn("Beta", names)

    def test_invalid_status_filter_returns_400(self):
        self._auth(self.admin_user)
        response = self.client.get(reverse("core:project_list"), {"status": "bogus"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_date_filter_returns_400(self):
        self._auth(self.admin_user)
        response = self.client.get(
            reverse("core:project_list"), {"active_from": "not-a-date"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ──────────────────────────────────────
    # Detail
    # ──────────────────────────────────────

    def test_detail_includes_active_members(self):
        self._auth(self.admin_user)
        response = self.client.get(reverse("core:project_detail", args=[self.alpha.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Alpha")
        member_ids = {m["user_profile_id"] for m in response.data["active_members"]}
        self.assertEqual(member_ids, {self.employee_user.profile.id})
        member = response.data["active_members"][0]
        self.assertEqual(member["weekly_allocation_hours"], "40.00")

    def test_assignment_create_accepts_weekly_allocation_hours(self):
        self._auth(self.hr_user)

        response = self.client.post(
            reverse("core:project_assignment_list", args=[self.gamma.pk]),
            {
                "user_profile_id": self.outsider_user.profile.id,
                "role": "Developer",
                "weekly_allocation_hours": "12.50",
                "start_date": "2026-05-01",
                "status": ProjectAssignmentStatus.ACTIVE,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["weekly_allocation_hours"], "12.50")
        self.assertEqual(response.data["allocation_percentage"], 31)

    def test_assignment_rejects_mismatched_hours_and_percentage(self):
        self._auth(self.hr_user)

        response = self.client.post(
            reverse("core:project_assignment_list", args=[self.gamma.pk]),
            {
                "user_profile_id": self.outsider_user.profile.id,
                "allocation_percentage": 50,
                "weekly_allocation_hours": "12.50",
                "start_date": "2026-05-01",
                "status": ProjectAssignmentStatus.ACTIVE,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("weekly_allocation_hours", response.data)

    @patch("core.views.timezone.localdate", return_value=date(2026, 5, 21))
    def test_assignment_allocation_change_only_affects_today_and_future(self, _today):
        assignment = ProjectAssignment.objects.create(
            user_profile=self.outsider_user.profile,
            project=self.gamma,
            allocation_percentage=50,
            weekly_allocation_hours="20.00",
            start_date=date(2026, 5, 1),
            status=ProjectAssignmentStatus.ACTIVE,
        )
        self._auth(self.hr_user)

        response = self.client.patch(
            reverse("core:project_assignment_detail", args=[assignment.pk]),
            {"weekly_allocation_hours": "10.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        assignment.refresh_from_db()
        new_assignment = ProjectAssignment.objects.get(pk=response.data["id"])
        self.assertNotEqual(new_assignment.id, assignment.id)
        self.assertEqual(assignment.end_date, date(2026, 5, 20))
        self.assertEqual(assignment.status, ProjectAssignmentStatus.COMPLETED)
        self.assertEqual(new_assignment.start_date, date(2026, 5, 21))
        self.assertEqual(new_assignment.weekly_allocation_hours, 10)
        self.assertEqual(new_assignment.allocation_percentage, 25)

        past = weekly_allocation_summary(
            employee=self.outsider_user.profile,
            week_start=date(2026, 5, 11),
        )
        changed_week = weekly_allocation_summary(
            employee=self.outsider_user.profile,
            week_start=date(2026, 5, 18),
        )
        future = weekly_allocation_summary(
            employee=self.outsider_user.profile,
            week_start=date(2026, 5, 25),
        )

        self.assertEqual(past["planned_hours"], "20.00")
        self.assertEqual(changed_week["planned_hours"], "16.00")
        self.assertEqual(future["planned_hours"], "10.00")

    def test_assignment_create_closes_previous_overlap_for_same_member_project(self):
        existing = ProjectAssignment.objects.create(
            user_profile=self.outsider_user.profile,
            project=self.gamma,
            allocation_percentage=50,
            weekly_allocation_hours="20.00",
            start_date=date(2026, 5, 1),
            status=ProjectAssignmentStatus.ACTIVE,
        )
        self._auth(self.hr_user)

        response = self.client.post(
            reverse("core:project_assignment_list", args=[self.gamma.pk]),
            {
                "user_profile_id": self.outsider_user.profile.id,
                "weekly_allocation_hours": "10.00",
                "start_date": "2026-05-21",
                "status": ProjectAssignmentStatus.ACTIVE,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        existing.refresh_from_db()
        self.assertEqual(existing.end_date, date(2026, 5, 20))
        self.assertEqual(existing.status, ProjectAssignmentStatus.COMPLETED)
        changed_week = weekly_allocation_summary(
            employee=self.outsider_user.profile,
            week_start=date(2026, 5, 18),
        )
        self.assertEqual(changed_week["planned_hours"], "16.00")

    def test_detail_not_found(self):
        self._auth(self.admin_user)
        response = self.client.get(reverse("core:project_detail", args=[99999]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_forbidden_for_outsider(self):
        self._auth(self.outsider_user)
        response = self.client.get(reverse("core:project_detail", args=[self.alpha.pk]))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ──────────────────────────────────────
    # Create
    # ──────────────────────────────────────

    def test_create_succeeds_for_hr(self):
        self._auth(self.hr_user)
        response = self.client.post(
            reverse("core:project_list"),
            {
                "name": "Delta",
                "client": "Initech",
                "project_type": ProjectType.CLIENT,
                "status": ProjectStatus.PLANNED,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Project.objects.filter(name="Delta").exists())

    def test_create_validation_error_missing_name(self):
        self._auth(self.hr_user)
        response = self.client.post(
            reverse("core:project_list"),
            {"client": "Acme"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_create_client_required_for_client_project(self):
        self._auth(self.hr_user)
        response = self.client.post(
            reverse("core:project_list"),
            {"name": "Epsilon", "project_type": ProjectType.CLIENT},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_forbidden_for_employee(self):
        self._auth(self.employee_user)
        response = self.client.post(
            reverse("core:project_list"),
            {"name": "X", "project_type": ProjectType.INTERNAL},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_forbidden_for_manager(self):
        self._auth(self.manager_user)
        response = self.client.post(
            reverse("core:project_list"),
            {"name": "X", "project_type": ProjectType.INTERNAL},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ──────────────────────────────────────
    # Update
    # ──────────────────────────────────────

    def test_patch_updates_fields(self):
        self._auth(self.hr_user)
        response = self.client.patch(
            reverse("core:project_detail", args=[self.alpha.pk]),
            {"description": "New description"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alpha.refresh_from_db()
        self.assertEqual(self.alpha.description, "New description")

    def test_update_end_before_start_rejected(self):
        self._auth(self.hr_user)
        response = self.client.patch(
            reverse("core:project_detail", args=[self.alpha.pk]),
            {"end_date": "2025-01-01"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_date", response.data)

    def test_update_forbidden_for_employee(self):
        self._auth(self.employee_user)
        response = self.client.patch(
            reverse("core:project_detail", args=[self.alpha.pk]),
            {"description": "nope"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_not_found(self):
        self._auth(self.hr_user)
        response = self.client.patch(
            reverse("core:project_detail", args=[99999]),
            {"description": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ──────────────────────────────────────
    # Archive / reactivate
    # ──────────────────────────────────────

    def test_archive_sets_archived_status(self):
        self._auth(self.admin_user)
        response = self.client.post(
            reverse("core:project_archive", args=[self.alpha.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alpha.refresh_from_db()
        self.assertEqual(self.alpha.status, ProjectStatus.ARCHIVED)

    def test_archive_forbidden_for_employee(self):
        self._auth(self.employee_user)
        response = self.client.post(
            reverse("core:project_archive", args=[self.alpha.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_archive_not_found(self):
        self._auth(self.admin_user)
        response = self.client.post(reverse("core:project_archive", args=[99999]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_reactivate_restores_active_status(self):
        self.alpha.status = ProjectStatus.ARCHIVED
        self.alpha.save(update_fields=["status"])
        self._auth(self.admin_user)
        response = self.client.post(
            reverse("core:project_reactivate", args=[self.alpha.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.alpha.refresh_from_db()
        self.assertEqual(self.alpha.status, ProjectStatus.ACTIVE)

    def test_reactivate_forbidden_for_employee(self):
        self._auth(self.employee_user)
        response = self.client.post(
            reverse("core:project_reactivate", args=[self.alpha.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
