"""Seed CPFLevel.display_name + CPFLevel.career_level for dev DB.

Idempotent — safe to re-run. Skips levels whose values already set unless
--force passed (then overwrites).

Usage:
    python manage.py seed_cpf_level_metadata
    python manage.py seed_cpf_level_metadata --dry-run
    python manage.py seed_cpf_level_metadata --force
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import CPFLevel

CAREER_LEVEL_BY_TIER = {
    1: "Junior",
    2: "Mid",
    3: "Senior",
    4: "Lead",
    5: "Principal",
}

ROLE_LABEL_BY_PREFIX = {
    "AI": "AI Engineer",
    "DE": "Data Engineer",
    "ME": "Mobile Engineer",
    "FS": "Full Stack Engineer",
    "TL": "Tech Lead",
    "SA": "Solution Architect",
    "DO": "DevOps Engineer",
    "QA": "QA Engineer",
    "QL": "QA Engineer",
    "PM": "Project Manager",
    "UI": "UI/UX Engineer",
    "UL": "UI/UX Engineer",
}


def _display(career: str, role_label: str) -> str:
    if career.lower() in role_label.lower():
        return role_label
    return f"{career} {role_label}"


# (code, tier, role_prefix) — mirrors seed_cpf_compensation.CPF_LEVELS.
CPF_METADATA: list[tuple[str, int, str]] = [
    ("AI1", 1, "AI"),
    ("AI2", 2, "AI"),
    ("AI3", 3, "AI"),
    ("DE1", 1, "DE"),
    ("DE2", 2, "DE"),
    ("DE3", 3, "DE"),
    ("ME1", 1, "ME"),
    ("ME2", 2, "ME"),
    ("ME3", 3, "ME"),
    ("FS1", 1, "FS"),
    ("FS2", 2, "FS"),
    ("FS3", 3, "FS"),
    ("FS4", 4, "FS"),
    ("TL4", 4, "TL"),
    ("TL5", 5, "TL"),
    ("SA4", 4, "SA"),
    ("SA5", 5, "SA"),
    ("DO1", 1, "DO"),
    ("DO2", 2, "DO"),
    ("DO3", 3, "DO"),
    ("DO4", 4, "DO"),
    ("DO5", 5, "DO"),
    ("QA1", 1, "QA"),
    ("QA2", 2, "QA"),
    ("QA3", 3, "QA"),
    ("QL4", 4, "QL"),
    ("QL5", 5, "QL"),
    ("PM1", 1, "PM"),
    ("PM2", 2, "PM"),
    ("PM3", 3, "PM"),
    ("PM4", 4, "PM"),
    ("UI1", 1, "UI"),
    ("UI2", 2, "UI"),
    ("UI3", 3, "UI"),
    ("UL4", 4, "UL"),
    ("UL5", 5, "UL"),
]


class Command(BaseCommand):
    help = "Seed CPFLevel.display_name + career_level."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing display_name/career_level values.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        dry = bool(opts.get("dry_run"))
        force = bool(opts.get("force"))
        updated = skipped = missing = 0

        for code, tier, prefix in CPF_METADATA:
            try:
                cpf = CPFLevel.objects.get(name=code)
            except CPFLevel.DoesNotExist:
                missing += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  missing CPFLevel name={code} — run seed_cpf_compensation first"
                    )
                )
                continue

            career = CAREER_LEVEL_BY_TIER[tier]
            role_label = ROLE_LABEL_BY_PREFIX[prefix]
            display = _display(career, role_label)

            changed_fields = []
            if force or not cpf.display_name:
                if cpf.display_name != display:
                    cpf.display_name = display
                    changed_fields.append("display_name")
            if force or not cpf.career_level:
                if cpf.career_level != career:
                    cpf.career_level = career
                    changed_fields.append("career_level")

            if changed_fields:
                cpf.save()
                updated += 1
                self.stdout.write(
                    f"  updated {code}: {', '.join(changed_fields)} "
                    f"→ display_name={cpf.display_name!r} career_level={cpf.career_level!r}"
                )
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. updated={updated} skipped={skipped} missing={missing}"
            )
        )

        if dry:
            self.stdout.write(self.style.WARNING("Dry run — rolling back."))
            transaction.set_rollback(True)
