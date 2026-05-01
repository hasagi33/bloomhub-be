"""
Tests for Training & Development API endpoints.
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import TrainingEntry, UserProfile


class TrainingEntryAPITestCase(APITestCase):
    """Test cases for TrainingEntry API endpoints."""

    @staticmethod
    def _extract_results(payload):
        """Extract results from paginated or non-paginated response."""
        if isinstance(payload, dict) and "results" in payload:
            return payload["results"]
        return payload

    def setUp(self):
        """Set up test data."""
        # Create users
        self.emp_user = User.objects.create_user(
            username="employee",
            email="emp@test.com",
            password="testpass123",
        )
        self.emp_user.save()

        self.hr_user = User.objects.create_user(
            username="hr",
            email="hr@test.com",
            password="testpass123",
            is_staff=True,
        )
        self.hr_user.save()

        # Ensure profiles exist
        self.emp_profile = UserProfile.objects.get(user=self.emp_user)
        self.hr_profile = UserProfile.objects.get(user=self.hr_user)

        # Create another employee for testing
        self.other_emp_user = User.objects.create_user(
            username="employee2",
            email="emp2@test.com",
            password="testpass123",
        )
        self.other_emp_user.save()
        self.other_emp_profile = UserProfile.objects.get(user=self.other_emp_user)

        # Create test training entry
        self.training_entry = TrainingEntry.objects.create(
            employee=self.emp_profile,
            course_title="Python Advanced",
            provider="Coursera",
            training_date=timezone.now().date() - timedelta(days=10),
            training_type=TrainingEntry.TrainingType.COURSE,
            cost=99.99,
            description="Advanced Python course",
        )

    def test_employee_list_own_trainings(self):
        """Employees can list only their own training entries."""
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], self.training_entry.id)

    def test_employee_cannot_list_other_trainings(self):
        """Employees cannot see other employees' training entries."""
        # Create training for another employee
        TrainingEntry.objects.create(
            employee=self.other_emp_profile,
            course_title="Django Basics",
            provider="Udemy",
            training_date=timezone.now().date() - timedelta(days=5),
            training_type=TrainingEntry.TrainingType.COURSE,
            cost=49.99,
        )

        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should only see own training
        results = self._extract_results(response.json())
        self.assertEqual(len(results), 1)

    def test_hr_list_all_trainings(self):
        """HR can list all training entries."""
        # Create training for another employee
        TrainingEntry.objects.create(
            employee=self.other_emp_profile,
            course_title="Django Basics",
            provider="Udemy",
            training_date=timezone.now().date() - timedelta(days=5),
            training_type=TrainingEntry.TrainingType.COURSE,
            cost=49.99,
        )

        self.client.force_authenticate(user=self.hr_user)
        response = self.client.get("/api/training-entries/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # HR should see all trainings (2)
        results = self._extract_results(response.json())
        self.assertEqual(len(results), 2)

    def test_create_training_entry_employee(self):
        """Employee can create training entry for themselves."""
        self.client.force_authenticate(user=self.emp_user)
        data = {
            "course_title": "AWS Certification",
            "provider": "AWS Training",
            "training_date": (timezone.now().date() - timedelta(days=3)).isoformat(),
            "training_type": "certification",
            "cost": "299.99",
            "description": "AWS Solutions Architect Exam",
        }
        response = self.client.post("/api/training-entries/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        resp_data = response.json()
        self.assertEqual(resp_data["employee_id"], self.emp_profile.id)
        self.assertEqual(resp_data["course_title"], "AWS Certification")

    def test_hr_create_training_for_employee(self):
        """HR can create training entry for specific employee."""
        self.client.force_authenticate(user=self.hr_user)
        data = {
            "course_title": "Leadership Workshop",
            "provider": "LinkedIn Learning",
            "training_date": (timezone.now().date() - timedelta(days=2)).isoformat(),
            "training_type": "workshop",
            "employee_id": self.other_emp_profile.id,
        }
        response = self.client.post("/api/training-entries/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        resp_data = response.json()
        self.assertEqual(resp_data["employee_id"], self.other_emp_profile.id)

    def test_validation_future_training_date(self):
        """Cannot create training with future date."""
        self.client.force_authenticate(user=self.emp_user)
        future_date = (timezone.now().date() + timedelta(days=5)).isoformat()
        data = {
            "course_title": "Future Course",
            "provider": "Test Provider",
            "training_date": future_date,
            "training_type": "course",
        }
        response = self.client.post("/api/training-entries/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("training_date", response.data)

    def test_validation_negative_cost(self):
        """Cannot create training with negative cost."""
        self.client.force_authenticate(user=self.emp_user)
        data = {
            "course_title": "Negative Cost Course",
            "provider": "Test Provider",
            "training_date": (timezone.now().date() - timedelta(days=1)).isoformat(),
            "training_type": "course",
            "cost": "-50.00",
        }
        response = self.client.post("/api/training-entries/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_validation_completed_after_training_date(self):
        """Completed date must be after training date."""
        self.client.force_authenticate(user=self.emp_user)
        training_date = timezone.now().date() - timedelta(days=10)
        completed_date = (timezone.now() - timedelta(days=15)).isoformat()

        data = {
            "course_title": "Test Course",
            "provider": "Test Provider",
            "training_date": training_date.isoformat(),
            "training_type": "course",
            "completed_at": completed_date,
        }
        response = self.client.post("/api/training-entries/", data, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("completed_at", response.data)

    def test_filter_by_type(self):
        """Can filter trainings by type."""
        TrainingEntry.objects.create(
            employee=self.emp_profile,
            course_title="AWS Conference",
            provider="AWS",
            training_date=timezone.now().date() - timedelta(days=5),
            training_type=TrainingEntry.TrainingType.CONFERENCE,
            cost=500.00,
        )

        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/?training_type=conference")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["training_type"], "conference")

    def test_filter_by_year(self):
        """Can filter trainings by year."""
        current_year = timezone.now().year

        # Create training for current year
        TrainingEntry.objects.create(
            employee=self.emp_profile,
            course_title="This Year Course",
            provider="Provider",
            training_date=timezone.now().date(),
            training_type=TrainingEntry.TrainingType.COURSE,
        )

        self.client.force_authenticate(user=self.emp_user)
        # Filter by current year
        response = self.client.get(f"/api/training-entries/?year={current_year}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should get at least 2 trainings from current year
        results = self._extract_results(response.json())
        self.assertGreaterEqual(len(results), 2)

    def test_search_by_course_title(self):
        """Can search trainings by course title."""
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/?search=Python")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.json())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["course_title"], "Python Advanced")

    def test_search_by_provider(self):
        """Can search trainings by provider."""
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/?search=Coursera")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.json())
        self.assertEqual(len(results), 1)

    def test_retrieve_training_entry(self):
        """Can retrieve single training entry."""
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get(f"/api/training-entries/{self.training_entry.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        resp_data = response.json()
        self.assertEqual(resp_data["id"], self.training_entry.id)
        self.assertEqual(resp_data["course_title"], "Python Advanced")

    def test_update_training_entry(self):
        """Employee can update their own training entry."""
        self.client.force_authenticate(user=self.emp_user)
        data = {
            "course_title": "Python Advanced - Updated",
            "provider": "Coursera",
            "training_date": self.training_entry.training_date.isoformat(),
            "training_type": "course",
            "cost": "119.99",
        }
        response = self.client.put(
            f"/api/training-entries/{self.training_entry.id}/", data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        resp_data = response.json()
        self.assertEqual(resp_data["course_title"], "Python Advanced - Updated")

    def test_delete_training_entry(self):
        """Employee can delete their own training entry."""
        self.client.force_authenticate(user=self.emp_user)
        response = self.client.delete(
            f"/api/training-entries/{self.training_entry.id}/"
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Verify deleted
        self.assertFalse(
            TrainingEntry.objects.filter(id=self.training_entry.id).exists()
        )

    def test_employee_cannot_update_other_training(self):
        """Employee cannot update another employee's training."""
        other_training = TrainingEntry.objects.create(
            employee=self.other_emp_profile,
            course_title="Other's Course",
            provider="Provider",
            training_date=timezone.now().date() - timedelta(days=5),
            training_type=TrainingEntry.TrainingType.COURSE,
        )

        self.client.force_authenticate(user=self.emp_user)
        data = {
            "course_title": "Hacked Course",
            "provider": "Provider",
            "training_date": other_training.training_date.isoformat(),
            "training_type": "course",
        }
        response = self.client.put(
            f"/api/training-entries/{other_training.id}/", data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_hr_can_update_any_training(self):
        """HR can update any employee's training entry."""
        other_training = TrainingEntry.objects.create(
            employee=self.other_emp_profile,
            course_title="Other's Course",
            provider="Provider",
            training_date=timezone.now().date() - timedelta(days=5),
            training_type=TrainingEntry.TrainingType.COURSE,
        )

        self.client.force_authenticate(user=self.hr_user)
        data = {
            "course_title": "Updated by HR",
            "provider": "Provider",
            "training_date": other_training.training_date.isoformat(),
            "training_type": "course",
        }
        response = self.client.put(
            f"/api/training-entries/{other_training.id}/", data, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        resp_data = response.json()
        self.assertEqual(resp_data["course_title"], "Updated by HR")

    def test_unauthenticated_cannot_access(self):
        """Unauthenticated users cannot access training endpoints."""
        response = self.client.get("/api/training-entries/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_training_status_computed_field(self):
        """Training status is computed correctly (completed/in-progress/planned)."""
        # Completed training
        completed = TrainingEntry.objects.create(
            employee=self.emp_profile,
            course_title="Completed",
            provider="Provider",
            training_date=timezone.now().date() - timedelta(days=10),
            training_type=TrainingEntry.TrainingType.COURSE,
            completed_at=timezone.now() - timedelta(days=5),
        )

        # Planned training
        planned = TrainingEntry.objects.create(
            employee=self.emp_profile,
            course_title="Planned",
            provider="Provider",
            training_date=timezone.now().date() + timedelta(days=10),
            training_type=TrainingEntry.TrainingType.COURSE,
        )

        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/")
        results_list = self._extract_results(response.json())
        results = {r["id"]: r["status"] for r in results_list}

        self.assertEqual(results[completed.id], "completed")
        self.assertEqual(results[planned.id], "planned")
        # In progress training (training_date in past, no completed_at)
        self.assertEqual(results[self.training_entry.id], "in-progress")

    def test_list_ordering_by_date(self):
        """Training entries are ordered by date (most recent first)."""
        older = TrainingEntry.objects.create(
            employee=self.emp_profile,
            course_title="Older Course",
            provider="Provider",
            training_date=timezone.now().date() - timedelta(days=30),
            training_type=TrainingEntry.TrainingType.COURSE,
        )

        self.client.force_authenticate(user=self.emp_user)
        response = self.client.get("/api/training-entries/")
        results = self._extract_results(response.json())
        ids = [r["id"] for r in results]

        # Most recent should come first
        self.assertEqual(ids[0], self.training_entry.id)
        self.assertEqual(ids[1], older.id)
