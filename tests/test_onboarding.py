from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    ChecklistInstance,
    ChecklistTask,
    ChecklistTemplate,
    Role,
    TaskTemplate,
    UserProfile,
)


class OnboardingModelTestCase(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.user = User.objects.create_user(
            username="john", email="john@test.com", password="pass"
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)

        self.template = ChecklistTemplate.objects.create(
            name="Standard IT Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
            role_responsible=ChecklistTemplate.Role.IT,
        )

        self.task_template = TaskTemplate.objects.create(
            checklist_template=self.template,
            title="Set up laptop",
            order=1,
        )

    def test_checklist_template_created(self):
        self.assertEqual(ChecklistTemplate.objects.count(), 1)
        self.assertEqual(self.template.name, "Standard IT Onboarding")
        self.assertEqual(self.template.type, ChecklistTemplate.Type.ONBOARDING)
        self.assertEqual(self.template.role_responsible, ChecklistTemplate.Role.IT)

    def test_task_template_linked_to_template(self):
        self.assertEqual(self.template.task_templates.count(), 1)
        self.assertEqual(self.task_template.title, "Set up laptop")

    def test_checklist_instance_created_for_employee(self):
        instance = ChecklistInstance.objects.create(
            employee=self.profile,
            template=self.template,
        )
        self.assertEqual(instance.status, ChecklistInstance.Status.IN_PROGRESS)
        self.assertEqual(instance.employee, self.profile)

    def test_checklist_task_created(self):
        instance = ChecklistInstance.objects.create(
            employee=self.profile,
            template=self.template,
        )
        task = ChecklistTask.objects.create(
            checklist_instance=instance,
            task_template=self.task_template,
            title="Set up laptop",
        )
        self.assertEqual(task.status, ChecklistTask.Status.TODO)
        self.assertIsNone(task.due_date)
        self.assertIsNone(task.completed_at)

    def test_offboarding_template_type(self):
        offboarding = ChecklistTemplate.objects.create(
            name="Standard Offboarding",
            type=ChecklistTemplate.Type.OFFBOARDING,
            role_responsible=ChecklistTemplate.Role.HR,
        )
        self.assertEqual(offboarding.type, ChecklistTemplate.Type.OFFBOARDING)


class ChecklistTemplateAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_user.is_staff = True
        self.hr_user.save()

        self.regular_user = User.objects.create_user(
            username="regular", email="regular@test.com", password="pass"
        )

        self.template = ChecklistTemplate.objects.create(
            name="IT Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
            role_responsible=ChecklistTemplate.Role.IT,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template,
            title="Set up laptop",
            order=1,
        )

    def test_hr_can_list_templates(self):
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.get("/api/onboarding/templates/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_hr_can_create_template(self):
        self.client.force_authenticate(user=self.hr_user)
        data = {
            "name": "HR Offboarding",
            "type": "offboarding",
            "role_responsible": "HR",
            "task_templates": [{"title": "Exit interview", "order": 1}],
        }
        res = self.client.post("/api/onboarding/templates/", data, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "HR Offboarding")
        self.assertEqual(res.data["role_responsible"], "HR")
        self.assertEqual(len(res.data["task_templates"]), 1)

    def test_hr_can_update_template(self):
        self.client.force_authenticate(user=self.hr_user)
        data = {"name": "Updated IT Onboarding", "type": "onboarding"}
        res = self.client.patch(
            f"/api/onboarding/templates/{self.template.id}/", data, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["name"], "Updated IT Onboarding")

    def test_hr_can_delete_template(self):
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.delete(f"/api/onboarding/templates/{self.template.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_hr_can_clone_template(self):
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.post(f"/api/onboarding/templates/{self.template.id}/clone/")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "IT Onboarding (Copy)")
        self.assertEqual(res.data["role_responsible"], "IT")
        self.assertEqual(len(res.data["task_templates"]), 1)

    def test_regular_user_cannot_access_templates(self):
        self.client.force_authenticate(user=self.regular_user)
        res = self.client.get("/api/onboarding/templates/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class ChecklistTaskAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.it_role = Role.objects.create(name="IT")

        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_user.is_staff = True
        self.hr_user.save()
        self.hr_profile, _ = UserProfile.objects.get_or_create(user=self.hr_user)

        self.it_user = User.objects.create_user(
            username="it", email="it@test.com", password="pass"
        )
        self.it_profile, _ = UserProfile.objects.get_or_create(user=self.it_user)
        self.it_profile.role = self.it_role
        self.it_profile.save()

        self.manager_user = User.objects.create_user(
            username="manager", email="manager@test.com", password="pass"
        )
        self.manager_profile, _ = UserProfile.objects.get_or_create(
            user=self.manager_user
        )

        self.employee_user = User.objects.create_user(
            username="employee", email="employee@test.com", password="pass"
        )
        self.employee_profile, _ = UserProfile.objects.get_or_create(
            user=self.employee_user
        )
        self.employee_profile.managers.add(self.manager_profile)

        # Template is IT-owned — all tasks will be assigned to IT staff
        self.template = ChecklistTemplate.objects.create(
            name="IT Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
            role_responsible=ChecklistTemplate.Role.IT,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template, title="Set up laptop", order=1
        )
        TaskTemplate.objects.create(
            checklist_template=self.template, title="Create accounts", order=2
        )
        TaskTemplate.objects.create(
            checklist_template=self.template, title="Provision access", order=3
        )

        self.instance = ChecklistInstance.objects.create(
            employee=self.employee_profile,
            template=self.template,
        )

    def test_checklist_tasks_are_created_and_assigned_by_role(self):
        self.assertEqual(self.instance.tasks.count(), 3)
        # All tasks should be assigned to the IT user (template role = IT)
        for task in self.instance.tasks.all():
            self.assertEqual(task.assigned_to, self.it_profile)

    def test_my_tasks_endpoint_returns_assigned_tasks(self):
        self.client.force_authenticate(user=self.it_user)
        res = self.client.get("/api/onboarding/tasks/my-tasks/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 3)

    def test_hr_can_view_employee_tasks(self):
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.get(
            f"/api/onboarding/tasks/employee/{self.employee_profile.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 3)

    def test_manager_can_view_direct_report_tasks(self):
        self.client.force_authenticate(user=self.manager_user)
        res = self.client.get(
            f"/api/onboarding/tasks/employee/{self.employee_profile.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 3)

    def test_regular_user_cannot_view_other_employee_tasks(self):
        other_user = User.objects.create_user(
            username="other", email="other@test.com", password="pass"
        )
        self.client.force_authenticate(user=other_user)
        res = self.client.get(
            f"/api/onboarding/tasks/employee/{self.employee_profile.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def _get_any_task(self):
        return self.instance.tasks.first()

    def test_assigned_user_can_update_status(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.it_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/",
            {"status": "in_progress"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "in_progress")
        task.refresh_from_db()
        self.assertEqual(task.status, "in_progress")
        self.assertIsNone(task.completed_at)

    def test_done_status_sets_completed_at(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.it_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/", {"status": "done"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertIsNotNone(task.completed_at)

    def test_moving_away_from_done_clears_completed_at(self):
        task = self._get_any_task()
        task.status = "done"
        task.completed_at = timezone.now()
        task.save()
        self.client.force_authenticate(user=self.it_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/", {"status": "todo"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertIsNone(task.completed_at)

    def test_hr_can_update_any_task_status(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/", {"status": "done"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_unassigned_user_cannot_update_status(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.manager_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/",
            {"status": "in_progress"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_status_value_rejected(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.it_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/", {"status": "invalid"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_method_not_allowed(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.it_user)
        res = self.client.put(
            f"/api/onboarding/tasks/{task.id}/", {"status": "done"}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_response_includes_nested_checklist_instance(self):
        task = self._get_any_task()
        self.client.force_authenticate(user=self.it_user)
        res = self.client.patch(
            f"/api/onboarding/tasks/{task.id}/",
            {"status": "in_progress"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("checklist_instance", res.data)
        self.assertIn("employee", res.data["checklist_instance"])
        self.assertIn("template", res.data["checklist_instance"])


class ChecklistInstanceAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        self.hr_role = Role.objects.create(name="HR")
        self.manager_role = Role.objects.create(name="Manager")

        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_user.is_staff = True
        self.hr_user.save()
        self.hr_profile, _ = UserProfile.objects.get_or_create(user=self.hr_user)
        self.hr_profile.role = self.hr_role
        self.hr_profile.save()

        self.manager_user = User.objects.create_user(
            username="manager", email="manager@test.com", password="pass"
        )
        self.manager_profile, _ = UserProfile.objects.get_or_create(
            user=self.manager_user
        )
        self.manager_profile.role = self.manager_role
        self.manager_profile.save()

        self.regular_user = User.objects.create_user(
            username="regular", email="regular@test.com", password="pass"
        )
        self.regular_profile, _ = UserProfile.objects.get_or_create(
            user=self.regular_user
        )

        self.employee_user = User.objects.create_user(
            username="employee", email="employee@test.com", password="pass"
        )
        self.employee_profile, _ = UserProfile.objects.get_or_create(
            user=self.employee_user
        )

        self.template = ChecklistTemplate.objects.create(
            name="HR Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
            role_responsible=ChecklistTemplate.Role.HR,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template, title="Paperwork", order=1
        )

    def test_hr_can_assign_template_to_employee(self):
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.post(
            "/api/onboarding/instances/",
            {"employee": self.employee_profile.id, "template": self.template.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["template"]["id"], self.template.id)
        self.assertEqual(res.data["employee"]["id"], self.employee_profile.id)

    def test_tasks_are_auto_created_on_assignment(self):
        self.client.force_authenticate(user=self.hr_user)
        self.client.post(
            "/api/onboarding/instances/",
            {"employee": self.employee_profile.id, "template": self.template.id},
            format="json",
        )
        instance = ChecklistInstance.objects.get(
            employee=self.employee_profile, template=self.template
        )
        self.assertEqual(instance.tasks.count(), 1)
        self.assertEqual(instance.tasks.first().title, "Paperwork")

    def test_manager_can_assign_template_to_employee(self):
        self.client.force_authenticate(user=self.manager_user)
        res = self.client.post(
            "/api/onboarding/instances/",
            {"employee": self.employee_profile.id, "template": self.template.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_regular_user_cannot_assign_template(self):
        self.client.force_authenticate(user=self.regular_user)
        res = self.client.post(
            "/api/onboarding/instances/",
            {"employee": self.employee_profile.id, "template": self.template.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_duplicate_assignment_is_rejected(self):
        ChecklistInstance.objects.create(
            employee=self.employee_profile, template=self.template
        )
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.post(
            "/api/onboarding/instances/",
            {"employee": self.employee_profile.id, "template": self.template.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hr_can_list_instances(self):
        ChecklistInstance.objects.create(
            employee=self.employee_profile, template=self.template
        )
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.get("/api/onboarding/instances/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(res.data), 1)

    def test_hr_can_delete_instance(self):
        instance = ChecklistInstance.objects.create(
            employee=self.employee_profile, template=self.template
        )
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.delete(f"/api/onboarding/instances/{instance.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChecklistInstance.objects.filter(id=instance.id).exists())

    def test_invalid_employee_returns_404(self):
        self.client.force_authenticate(user=self.hr_user)
        res = self.client.post(
            "/api/onboarding/instances/",
            {"employee": 99999, "template": self.template.id},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
