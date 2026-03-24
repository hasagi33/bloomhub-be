from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

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
