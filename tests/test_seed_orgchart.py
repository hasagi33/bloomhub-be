from django.core.management import call_command
from rest_framework.test import APITestCase

from core.models import (
    Department,
    Project,
    ProjectAssignment,
    Role,
    UserProfile,
)


class SeedOrgChartCommandTestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def test_seed_idempotent_creates_expected_state(self):
        # First run
        call_command("seed_orgchart", verbosity=0)
        self.assertEqual(Department.objects.count(), 10)
        self.assertGreaterEqual(Role.objects.count(), 30)
        # 31 unique seed emails. setup_public_tenant may or may not add a
        # super-admin profile depending on tenant state.
        self.assertEqual(
            UserProfile.objects.filter(email_address__endswith="@bloomteq.com").count(),
            31,
        )
        self.assertEqual(Project.objects.count(), 9)
        first_assignments = ProjectAssignment.objects.count()
        # Every seeded employee assigned to at least one project.
        assigned_ids = set(
            ProjectAssignment.objects.values_list("user_profile_id", flat=True)
        )
        seeded_ids = set(
            UserProfile.objects.filter(
                email_address__endswith="@bloomteq.com"
            ).values_list("id", flat=True)
        )
        self.assertTrue(seeded_ids.issubset(assigned_ids))

        # Second run — idempotent: counts unchanged.
        call_command("seed_orgchart", verbosity=0)
        self.assertEqual(Department.objects.count(), 10)
        self.assertEqual(
            UserProfile.objects.filter(email_address__endswith="@bloomteq.com").count(),
            31,
        )
        self.assertEqual(Project.objects.count(), 9)
        self.assertEqual(ProjectAssignment.objects.count(), first_assignments)

    def test_seed_produces_valid_org_chart_response(self):
        call_command("seed_orgchart", verbosity=0)
        from django.contrib.auth.models import User

        ceo_user = User.objects.get(email="hana@bloomteq.com")
        self.client.force_authenticate(user=ceo_user)
        res = self.client.get("/api/org-chart/")
        self.assertEqual(res.status_code, 200)
        body = res.json()

        # 30 employees, 10 depts, 6 projects.
        seeded = [
            e
            for e in body["employees"]
            if e["email"] and e["email"].endswith("@bloomteq.com")
        ]
        self.assertEqual(len(seeded), 31)
        self.assertEqual(len(body["departments"]), 10)
        self.assertEqual(len(body["projects"]), 9)

        # CEO has no primary_manager and is_manager True.
        ceo = next(e for e in seeded if e["email"] == "hana@bloomteq.com")
        self.assertIsNone(ceo["primary_manager_id"])
        self.assertTrue(ceo["is_manager"])

        # All seeded employees have role + department.
        for e in seeded:
            self.assertIsNotNone(e["role"], f"{e['email']} missing role")
            self.assertIsNotNone(e["department_id"], f"{e['email']} missing department")
            self.assertIsNotNone(e["phone_number"])
            self.assertIsNotNone(e["start_date"])
