from datetime import date, timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.enums import (
    EmploymentStatus,
    OrgChartEventKind,
    ProjectAssignmentStatus,
    ProjectStatus,
    TrackedField,
)
from core.models import (
    Department,
    EmployeeProfileChangeHistory,
    Project,
    ProjectAssignment,
    Role,
    TechnologyTag,
    UserProfile,
)


class OrgChartAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.eng = Department.objects.create(
            name="Engineering", color="#4f46e5", color_soft="#eef2ff"
        )
        self.design = Department.objects.create(
            name="Design", color="#ea580c", color_soft="#fff7ed"
        )

        self.role_eng = Role.objects.create(name="Senior Backend Engineer")
        self.tag_go = TechnologyTag.objects.create(name="Go")

        # CEO — root of tree
        self.ceo_user = User.objects.create_user(
            username="ceo",
            email="ceo@test.com",
            password="pw",
            first_name="C",
            last_name="EO",
        )
        self.ceo = UserProfile.objects.get_or_create(user=self.ceo_user)[0]
        self.ceo.full_name = "C EO"
        self.ceo.department_fk = self.eng
        self.ceo.save()

        # Manager
        self.mgr_user = User.objects.create_user(
            username="mgr", email="mgr@test.com", password="pw"
        )
        self.mgr = UserProfile.objects.get_or_create(user=self.mgr_user)[0]
        self.mgr.full_name = "Mid Manager"
        self.mgr.department_fk = self.eng
        self.mgr.primary_manager = self.ceo
        self.mgr.role = self.role_eng
        self.mgr.save()

        # Report
        self.ic_user = User.objects.create_user(
            username="ic", email="ic@test.com", password="pw"
        )
        self.ic = UserProfile.objects.get_or_create(user=self.ic_user)[0]
        self.ic.full_name = "Ima Coder"
        self.ic.department_fk = self.eng
        self.ic.primary_manager = self.mgr
        self.ic.is_remote = True
        self.ic.role = self.role_eng
        self.ic.save()
        self.ic.tech_tags.add(self.tag_go)

        # Inactive — must be excluded
        inactive_user = User.objects.create_user(
            username="gone", email="gone@test.com", password="pw"
        )
        self.inactive = UserProfile.objects.get_or_create(user=inactive_user)[0]
        self.inactive.employment_status = EmploymentStatus.INACTIVE
        self.inactive.save()

        # Project + active assignment
        self.project = Project.objects.create(
            name="Atlas",
            status=ProjectStatus.ACTIVE,
            project_type="client",
            client="Acme",
        )
        ProjectAssignment.objects.create(
            user_profile=self.ic,
            project=self.project,
            start_date=date.today(),
            status=ProjectAssignmentStatus.ACTIVE,
        )

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    # ── /api/org-chart/ ─────────────────────────────────────────────────

    def test_org_chart_returns_active_only(self):
        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        body = res.json()
        ids = {e["id"] for e in body["employees"]}
        self.assertIn(self.ceo.id, ids)
        self.assertIn(self.mgr.id, ids)
        self.assertIn(self.ic.id, ids)
        self.assertNotIn(self.inactive.id, ids)

    def test_org_chart_tree_integrity_root_and_no_cycles(self):
        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/")
        body = res.json()
        by_id = {e["id"]: e for e in body["employees"]}

        # Exactly one root (primary_manager_id None) among returned.
        roots = [e for e in body["employees"] if e["primary_manager_id"] is None]
        self.assertGreaterEqual(len(roots), 1)
        self.assertIn(self.ceo.id, [r["id"] for r in roots])

        # Walking up from every node terminates at a root (no cycles).
        for emp in body["employees"]:
            seen = set()
            cur = emp["id"]
            while cur is not None:
                self.assertNotIn(cur, seen, "cycle detected")
                seen.add(cur)
                cur = by_id[cur]["primary_manager_id"] if cur in by_id else None

    def test_is_manager_derivation(self):
        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/")
        by_id = {e["id"]: e for e in res.json()["employees"]}
        self.assertTrue(by_id[self.ceo.id]["is_manager"])
        self.assertTrue(by_id[self.mgr.id]["is_manager"])
        self.assertFalse(by_id[self.ic.id]["is_manager"])

    def test_employee_payload_fields(self):
        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/")
        by_id = {e["id"]: e for e in res.json()["employees"]}
        ic = by_id[self.ic.id]
        self.assertEqual(ic["primary_manager_id"], self.mgr.id)
        self.assertEqual(ic["department_id"], self.eng.id)
        self.assertTrue(ic["is_remote"])
        self.assertEqual(ic["employment_status"], "active")
        self.assertEqual(ic["role"]["name"], "Senior Backend Engineer")
        self.assertIn("Go", ic["skills"])
        self.assertEqual(ic["technology_tags"][0]["name"], "Go")

    def test_departments_and_projects_in_payload(self):
        self._auth(self.ceo_user)
        body = self.client.get("/api/org-chart/").json()
        eng = next(d for d in body["departments"] if d["id"] == self.eng.id)
        self.assertEqual(eng["color"], "#4f46e5")
        self.assertEqual(eng["color_soft"], "#eef2ff")
        self.assertEqual(eng["employee_count"], 3)
        proj = next(p for p in body["projects"] if p["id"] == self.project.id)
        self.assertEqual(proj["member_ids"], [self.ic.id])

    def test_org_chart_requires_auth(self):
        res = self.client.get("/api/org-chart/")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── /api/departments/ ───────────────────────────────────────────────

    def test_departments_list_returns_objects(self):
        self._auth(self.ceo_user)
        res = self.client.get("/api/departments/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        body = res.json()
        self.assertIsInstance(body, list)
        eng = next(d for d in body if d["id"] == self.eng.id)
        self.assertEqual(eng["color"], "#4f46e5")
        self.assertEqual(eng["color_soft"], "#eef2ff")
        self.assertEqual(eng["employee_count"], 3)
        self.assertIsNone(eng["head_employee_id"])

    # ── /api/org-chart/recent-updates/ ──────────────────────────────────

    def test_recent_updates_promote_reassign_leave(self):
        # Create events
        EmployeeProfileChangeHistory.objects.create(
            employee=self.ic,
            field=TrackedField.ROLE,
            old_value="Junior",
            new_value="Senior Backend Engineer",
        )
        EmployeeProfileChangeHistory.objects.create(
            employee=self.ic,
            field=TrackedField.DEPARTMENT,
            old_value="Engineering",
            new_value="Design",
        )
        EmployeeProfileChangeHistory.objects.create(
            employee=self.ic,
            field=TrackedField.EMPLOYMENT_STATUS,
            old_value="active",
            new_value="on_leave",
        )

        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/recent-updates/?limit=20")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        kinds = {ev["kind"] for ev in res.json()["results"]}
        self.assertIn(OrgChartEventKind.PROMOTE, kinds)
        self.assertIn(OrgChartEventKind.REASSIGN, kinds)
        self.assertIn(OrgChartEventKind.LEAVE, kinds)
        self.assertIn(OrgChartEventKind.HIRE, kinds)

    def test_recent_updates_limit_capped(self):
        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/recent-updates/?limit=999")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(res.json()["results"]), 50)

    def test_recent_updates_since_filter(self):
        future = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._auth(self.ceo_user)
        res = self.client.get("/api/org-chart/recent-updates/", {"since": future})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()["results"], [])
