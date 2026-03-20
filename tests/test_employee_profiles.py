from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Permission, UserProfile


class EmployeeProfileTestCase(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        call_command("setup_public_tenant", "--domain", "testserver", verbosity=0)

    def setUp(self):
        # Create normal user
        self.normal_user = User.objects.create_user(
            username="normal", email="normal@test.com", password="pass"
        )
        self.normal_profile, _ = UserProfile.objects.get_or_create(
            user=self.normal_user
        )

        # Create HR admin user
        self.hr_user = User.objects.create_user(
            username="hr", email="hr@test.com", password="pass"
        )
        self.hr_profile, _ = UserProfile.objects.get_or_create(user=self.hr_user)

        perm, _ = Permission.objects.get_or_create(
            module_name="Employee Profiles", feature_action="view_all_profiles"
        )
        self.hr_profile.add_permission(perm)
        # Refresh user cache
        self.hr_user.refresh_from_db()
        self.normal_user.refresh_from_db()

    def test_hr_can_list_all_profiles(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)
        res = self.client.get("/api/employees/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Check if pagination is enabled
        data = res.json()
        if isinstance(data, dict) and "results" in data:
            data = data["results"]
        self.assertGreaterEqual(len(data), 2)

    def test_normal_user_list_only_own_profile(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)
        res = self.client.get("/api/employees/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.json()
        if isinstance(data, dict) and "results" in data:
            data = data["results"]

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["email"], "normal@test.com")

    def test_hr_can_create_employee(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)
        data = {
            "email": "new.employee@test.com",
            "first_name": "New",
            "last_name": "Employee",
            "department": "Engineering",
        }
        res = self.client.post("/api/employees/", data)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.filter(email="new.employee@test.com").count(), 1)
        self.assertEqual(
            UserProfile.objects.filter(email_address="new.employee@test.com").count(), 1
        )

    def test_normal_user_cannot_create_employee(self):
        normal_user = User.objects.get(id=self.normal_user.id)
        self.client.force_authenticate(user=normal_user)
        data = {"email": "hacker@test.com"}
        res = self.client.post("/api/employees/", data)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_soft_delete(self):
        hr_user = User.objects.get(id=self.hr_user.id)
        self.client.force_authenticate(user=hr_user)
        res = self.client.delete(f"/api/employees/{self.normal_profile.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # Verify soft delete
        self.normal_profile.refresh_from_db()
        self.assertFalse(self.normal_profile.is_active)
        self.assertEqual(
            self.normal_profile.employment_status, UserProfile.EmploymentStatus.INACTIVE
        )
