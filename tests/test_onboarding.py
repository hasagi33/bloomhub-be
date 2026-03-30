from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    ChecklistInstance,
    ChecklistTask,
    ChecklistTemplate,
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
