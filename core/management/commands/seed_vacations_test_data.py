"""
Idempotent seed for testing the Vacations module's covering-employee feature.

Creates two projects ("Project Alpha", "Project Beta"), four test employees
(alice/bob/carol on Alpha; dave on Beta), and optionally adds the superuser
to Alpha so they can test the covering-employee dropdown from their own login.

Usage:
    python manage.py seed_vacations_test_data
    python manage.py seed_vacations_test_data --no-include-superuser
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.enums import EmploymentStatus, ProjectAssignmentStatus
from core.models import Project, ProjectAssignment, UserProfile

User = get_user_model()

ALPHA_NAME = "Project Alpha"
BETA_NAME = "Project Beta"
DEFAULT_PASSWORD = "Test1234!"

ALPHA_TEAM = [
    {
        "username": "alice",
        "first_name": "Alice",
        "last_name": "Anderson",
        "email": "alice@example.com",
    },
    {
        "username": "bob",
        "first_name": "Bob",
        "last_name": "Brown",
        "email": "bob@example.com",
    },
    {
        "username": "carol",
        "first_name": "Carol",
        "last_name": "Clark",
        "email": "carol@example.com",
    },
]
BETA_TEAM = [
    {
        "username": "dave",
        "first_name": "Dave",
        "last_name": "Davis",
        "email": "dave@example.com",
    },
]


class Command(BaseCommand):
    help = "Seed test projects + employees for Vacations covering-employee testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-include-superuser",
            dest="include_superuser",
            action="store_false",
            help="Skip adding the existing superuser to Project Alpha.",
        )
        parser.set_defaults(include_superuser=True)

    @transaction.atomic
    def handle(self, *args, **options):
        alpha, _ = Project.objects.get_or_create(
            name=ALPHA_NAME, defaults={"description": "Vacations test project A"}
        )
        beta, _ = Project.objects.get_or_create(
            name=BETA_NAME, defaults={"description": "Vacations test project B"}
        )
        self.stdout.write(
            self.style.SUCCESS(f"Projects ready: {alpha.name}, {beta.name}")
        )

        for spec in ALPHA_TEAM:
            self._ensure_employee_assigned(spec, alpha)
        for spec in BETA_TEAM:
            self._ensure_employee_assigned(spec, beta)

        if options["include_superuser"]:
            superuser = User.objects.filter(is_superuser=True).order_by("id").first()
            if superuser is None:
                self.stdout.write(
                    self.style.WARNING(
                        "No superuser found — skipping superuser assignment."
                    )
                )
            else:
                profile = UserProfile.objects.filter(user=superuser).first()
                if profile is None:
                    profile = UserProfile.objects.create(
                        user=superuser,
                        full_name=superuser.get_full_name() or superuser.username,
                        employment_status=EmploymentStatus.ACTIVE,
                    )
                self._ensure_assignment(profile, alpha)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Superuser '{superuser.username}' assigned to {alpha.name}."
                    )
                )

        self.stdout.write(self.style.SUCCESS("Vacations test data seeded."))

    def _ensure_employee_assigned(self, spec: dict, project: Project) -> None:
        user, created = User.objects.get_or_create(
            username=spec["username"],
            defaults={
                "first_name": spec["first_name"],
                "last_name": spec["last_name"],
                "email": spec["email"],
            },
        )
        if created:
            user.set_password(DEFAULT_PASSWORD)
            user.save()
            self.stdout.write(
                f"  + Created user '{user.username}' (password: {DEFAULT_PASSWORD})"
            )

        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "full_name": f"{spec['first_name']} {spec['last_name']}",
                "email_address": spec["email"],
                "employment_status": EmploymentStatus.ACTIVE,
            },
        )
        self._ensure_assignment(profile, project)
        self.stdout.write(f"  - {user.username} -> {project.name}")

    def _ensure_assignment(self, profile: UserProfile, project: Project) -> None:
        ProjectAssignment.objects.get_or_create(
            user_profile=profile,
            project=project,
            defaults={
                "start_date": date.today(),
                "status": ProjectAssignmentStatus.ACTIVE,
            },
        )
