"""
Idempotent seed for the projects data model (BHB-524).

Creates one active client project, one internal project, two test employees,
one current assignment, and one historical assignment so the history
preservation behavior can be verified.

Usage:
    python manage.py seed_projects_data_model
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.enums import ProjectAssignmentStatus, ProjectStatus, ProjectType
from core.models import Project, ProjectAssignment, UserProfile

User = get_user_model()

ACTIVE_PROJECT_NAME = "Acme Portal"
INTERNAL_PROJECT_NAME = "Internal Tooling"


class Command(BaseCommand):
    help = "Seed sample projects and one historical + one current assignment."

    @transaction.atomic
    def handle(self, *args, **options):
        active_owner = self._ensure_employee(
            "pm_active", "Pat", "Manager", "pat.manager@example.com"
        )
        engineer = self._ensure_employee(
            "eng_seed", "Eve", "Engineer", "eve.engineer@example.com"
        )

        active_project, _ = Project.objects.update_or_create(
            name=ACTIVE_PROJECT_NAME,
            defaults={
                "description": "Active client engagement used for seed data.",
                "client": "Acme Corp",
                "project_type": ProjectType.CLIENT,
                "status": ProjectStatus.ACTIVE,
                "start_date": date(2026, 1, 1),
                "end_date": None,
                "owner": active_owner,
            },
        )

        internal_project, _ = Project.objects.update_or_create(
            name=INTERNAL_PROJECT_NAME,
            defaults={
                "description": "Internal tooling project (historical assignment).",
                "client": None,
                "project_type": ProjectType.INTERNAL,
                "status": ProjectStatus.COMPLETED,
                "start_date": date(2025, 1, 1),
                "end_date": date(2025, 12, 31),
                "owner": active_owner,
            },
        )

        ProjectAssignment.objects.get_or_create(
            user_profile=engineer,
            project=internal_project,
            start_date=date(2025, 1, 15),
            defaults={
                "role": "Backend Engineer",
                "allocation_percentage": 50,
                "end_date": date(2025, 12, 31),
                "status": ProjectAssignmentStatus.COMPLETED,
                "notes": "Historical assignment preserved for audit.",
            },
        )

        ProjectAssignment.objects.get_or_create(
            user_profile=engineer,
            project=active_project,
            start_date=date(2026, 1, 1),
            defaults={
                "role": "Backend Engineer",
                "allocation_percentage": 100,
                "end_date": None,
                "status": ProjectAssignmentStatus.ACTIVE,
                "notes": "Current assignment.",
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded projects + assignments "
                f"(active: {active_project.name}, historical: {internal_project.name})."
            )
        )

    def _ensure_employee(
        self, username: str, first: str, last: str, email: str
    ) -> UserProfile:
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first, "last_name": last, "email": email},
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile
