from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
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
        # Create a user and profile to act as the employee
        self.user = User.objects.create_user(
            username="john", email="john@test.com", password="pass"
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)

        # Create a checklist template
        self.template = ChecklistTemplate.objects.create(
            name="Standard IT Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
        )

        # Create a task template inside it
        self.task_template = TaskTemplate.objects.create(
            checklist_template=self.template,
            title="Set up laptop",
            order=1,
            role_responsible=TaskTemplate.Role.IT,
        )

    def test_checklist_template_created(self):
        self.assertEqual(ChecklistTemplate.objects.count(), 1)
        self.assertEqual(self.template.name, "Standard IT Onboarding")
        self.assertEqual(self.template.type, ChecklistTemplate.Type.ONBOARDING)

    def test_task_template_linked_to_template(self):
        self.assertEqual(self.template.task_templates.count(), 1)
        self.assertEqual(self.task_template.role_responsible, TaskTemplate.Role.IT)

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
        )
        self.assertEqual(offboarding.type, ChecklistTemplate.Type.OFFBOARDING)


class ChecklistTemplateAPITestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        # HR user
        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_user.is_staff = True
        self.hr_user.save()

        # Regular user
        self.regular_user = User.objects.create_user(
            username="regular", email="regular@test.com", password="pass"
        )

        # A template for testing
        self.template = ChecklistTemplate.objects.create(
            name="IT Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template,
            title="Set up laptop",
            order=1,
            role_responsible=TaskTemplate.Role.IT,
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
            "task_templates": [
                {"title": "Exit interview", "order": 1, "role_responsible": "HR"}
            ],
        }
        res = self.client.post("/api/onboarding/templates/", data, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "HR Offboarding")
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
        self.hr_role = Role.objects.create(name="HR")
        self.it_role = Role.objects.create(name="IT")

        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_user.is_staff = True
        self.hr_user.save()
        self.hr_profile, _ = UserProfile.objects.get_or_create(user=self.hr_user)
        self.hr_profile.role = self.hr_role
        self.hr_profile.save()

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

        self.template = ChecklistTemplate.objects.create(
            name="Standard Onboarding",
            type=ChecklistTemplate.Type.ONBOARDING,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template,
            title="HR onboarding",
            order=1,
            role_responsible=TaskTemplate.Role.HR,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template,
            title="IT onboarding",
            order=2,
            role_responsible=TaskTemplate.Role.IT,
        )
        TaskTemplate.objects.create(
            checklist_template=self.template,
            title="Manager review",
            order=3,
            role_responsible=TaskTemplate.Role.MANAGER,
        )

        self.instance = ChecklistInstance.objects.create(
            employee=self.employee_profile,
            template=self.template,
        )

    def test_checklist_tasks_are_created_and_assigned_by_role(self):
        self.assertEqual(self.instance.tasks.count(), 3)

        hr_task = self.instance.tasks.get(
            task_template__role_responsible=TaskTemplate.Role.HR
        )
        self.assertEqual(hr_task.assigned_to, self.hr_profile)

        it_task = self.instance.tasks.get(
            task_template__role_responsible=TaskTemplate.Role.IT
        )
        self.assertEqual(it_task.assigned_to, self.it_profile)

        manager_task = self.instance.tasks.get(
            task_template__role_responsible=TaskTemplate.Role.MANAGER
        )
        self.assertEqual(manager_task.assigned_to, self.manager_profile)

    def test_my_tasks_endpoint_returns_assigned_tasks(self):
        self.client.force_authenticate(user=self.it_user)
        res = self.client.get("/api/onboarding/tasks/my-tasks/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["title"], "IT onboarding")

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
        other_profile, _ = UserProfile.objects.get_or_create(user=other_user)
        self.client.force_authenticate(user=other_user)
        res = self.client.get(
            f"/api/onboarding/tasks/employee/{self.employee_profile.id}/"
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
