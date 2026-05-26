"""Seed CPF levels for Bloomteq employee profiles.

Idempotent. Safe to re-run.

Run after CPF levels and org-chart employees exist:
    python manage.py seed_cpf_compensation
    python manage.py seed_orgchart
    python manage.py seed_employee_cpf_levels

Use --force to overwrite existing cpf_level values.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

# Email -> CPF level. Keep this explicit so backend seeding, not admin UI,
# owns initial assignment choices.
CPF_ASSIGNMENTS: dict[str, str] = {
    "hana@bloomteq.com": "SA5",
    "senad@bloomteq.com": "TL5",
    "almir@bloomteq.com": "PM4",
    "lana@bloomteq.com": "PM4",
    "mirsad@bloomteq.com": "PM4",
    "aida@bloomteq.com": "PM4",
    "asmin@bloomteq.com": "TL4",
    "ahmed@bloomteq.com": "DO4",
    "mirza@bloomteq.com": "ME3",
    "lejla@bloomteq.com": "QL4",
    "damir@bloomteq.com": "TL4",
    "dzenana@bloomteq.com": "UL5",
    "sanja@bloomteq.com": "PM3",
    "selma@bloomteq.com": "PM3",
    "adisa@bloomteq.com": "PM3",
    "ena@bloomteq.com": "PM3",
    "tarik.p@bloomteq.com": "DO5",
    "tarik@bloomteq.com": "FS3",
    "vedad@bloomteq.com": "FS2",
    "muhamed@bloomteq.com": "FS2",
    "hanan@bloomteq.com": "FS2",
    "tarik.s@bloomteq.com": "FS1",
    "jasmin@bloomteq.com": "DO3",
    "kemal@bloomteq.com": "ME2",
    "maja@bloomteq.com": "UI2",
    "nadina@bloomteq.com": "UI2",
    "amila@bloomteq.com": "PM2",
    "ivana@bloomteq.com": "PM1",
    "edin@bloomteq.com": "PM2",
    "nedim@bloomteq.com": "DE2",
    "boris@bloomteq.com": "DO1",
}


class Command(BaseCommand):
    help = "Seed cpf_level values for Bloomteq employee profiles."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing cpf_level values.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        from core.models import CPFLevel, UserProfile

        dry = bool(opts.get("dry_run"))
        force = bool(opts.get("force"))

        valid_levels = set(CPFLevel.objects.values_list("name", flat=True))
        if not valid_levels:
            self.stdout.write(
                self.style.ERROR(
                    "No CPFLevel rows in DB. Run seed_cpf_compensation first."
                )
            )
            return

        missing_levels = sorted(set(CPF_ASSIGNMENTS.values()) - valid_levels)
        if missing_levels:
            self.stdout.write(
                self.style.ERROR("Missing CPFLevel rows: " + ", ".join(missing_levels))
            )
            return

        assigned = skipped_existing = missing_profiles = unchanged = 0
        details: list[str] = []

        for email, cpf_level in CPF_ASSIGNMENTS.items():
            profile = (
                UserProfile.objects.select_related("user")
                .filter(email_address__iexact=email)
                .first()
                or UserProfile.objects.select_related("user")
                .filter(user__email__iexact=email)
                .first()
                or UserProfile.objects.select_related("user")
                .filter(user__username__iexact=email)
                .first()
            )
            if profile is None:
                missing_profiles += 1
                details.append(f"  missing profile: {email} -> {cpf_level}")
                continue

            existing = (profile.cpf_level or "").strip()
            if existing == cpf_level:
                unchanged += 1
                continue
            if existing and not force:
                skipped_existing += 1
                details.append(
                    f"  skipped existing: {email} {existing} " f"(wanted {cpf_level})"
                )
                continue

            if not dry:
                profile.cpf_level = cpf_level
                profile.save(update_fields=["cpf_level"])
            assigned += 1
            details.append(f"  assigned: {email} -> {cpf_level}")

        if details:
            self.stdout.write("\n".join(details))

        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned: {assigned}  "
                f"Unchanged: {unchanged}  "
                f"Skipped existing: {skipped_existing}  "
                f"Missing profiles: {missing_profiles}"
            )
        )

        if dry:
            self.stdout.write(self.style.WARNING("Dry run - rolling back."))
            transaction.set_rollback(True)
