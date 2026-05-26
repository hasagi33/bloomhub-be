"""Seed CPF levels, roles, compensation policies, and the benefits catalog
from the Bloomteq Career Progression Framework.

Idempotent. Safe to re-run.

Salary tiering (BAM, NET monthly):
    Tier 1 (Beginner, 0+ years):   1000   (legal minimum, per HR)
    Tier 2 (Intermediate, 2+ y):   1500
    Tier 3 (Advanced / Senior 5+): 2200
    Tier 4 (Lead, 5+ y):           3000
    Tier 5 (Principal / Head):     4000

Benefits catalog seeded:
    Topli obrok (Meal allowance):    213 BAM / month
    Prijevoz (Transport allowance):   51 BAM / month

Usage:
    python manage.py seed_cpf_compensation
    python manage.py seed_cpf_compensation --dry-run
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

# ── Data extracted from the CPF Framework document ──────────────────────────
ROLES: list[tuple[str, str]] = [
    ("AI Engineer", "Builds and ships ML / AI features."),
    ("Data Engineer", "Owns ETL, pipelines, and data infrastructure."),
    ("Mobile Engineer", "iOS / Android native and cross-platform."),
    ("Full Stack Engineer", "End-to-end web product engineering."),
    ("Tech Lead", "Technical leadership for an engineering team."),
    ("Solution Architect", "Cross-team architectural ownership."),
    ("DevOps Engineer", "Infra, CI/CD, observability, cloud."),
    ("QA Engineer", "Quality engineering and test automation."),
    ("Project Manager", "Delivery, planning, stakeholder management."),
    ("UI/UX Engineer", "Product design and interaction."),
]

# (level name, role name, tier, order within role)
CPF_LEVELS: list[tuple[str, str, int, int]] = [
    # AI 1-3
    ("AI1", "AI Engineer", 1, 1),
    ("AI2", "AI Engineer", 2, 2),
    ("AI3", "AI Engineer", 3, 3),
    # Data 1-3
    ("DE1", "Data Engineer", 1, 1),
    ("DE2", "Data Engineer", 2, 2),
    ("DE3", "Data Engineer", 3, 3),
    # Mobile 1-3
    ("ME1", "Mobile Engineer", 1, 1),
    ("ME2", "Mobile Engineer", 2, 2),
    ("ME3", "Mobile Engineer", 3, 3),
    # Full Stack 1-4
    ("FS1", "Full Stack Engineer", 1, 1),
    ("FS2", "Full Stack Engineer", 2, 2),
    ("FS3", "Full Stack Engineer", 3, 3),
    ("FS4", "Full Stack Engineer", 4, 4),
    # Tech Lead 4-5
    ("TL4", "Tech Lead", 4, 1),
    ("TL5", "Tech Lead", 5, 2),
    # Solution Architect 4-5
    ("SA4", "Solution Architect", 4, 1),
    ("SA5", "Solution Architect", 5, 2),
    # DevOps 1-5
    ("DO1", "DevOps Engineer", 1, 1),
    ("DO2", "DevOps Engineer", 2, 2),
    ("DO3", "DevOps Engineer", 3, 3),
    ("DO4", "DevOps Engineer", 4, 4),
    ("DO5", "DevOps Engineer", 5, 5),
    # QA 1-5 (note: lead levels prefixed QL per the framework)
    ("QA1", "QA Engineer", 1, 1),
    ("QA2", "QA Engineer", 2, 2),
    ("QA3", "QA Engineer", 3, 3),
    ("QL4", "QA Engineer", 4, 4),
    ("QL5", "QA Engineer", 5, 5),
    # Project Manager 1-4
    ("PM1", "Project Manager", 1, 1),
    ("PM2", "Project Manager", 2, 2),
    ("PM3", "Project Manager", 3, 3),
    ("PM4", "Project Manager", 4, 4),
    # UI/UX 1-5 (lead levels prefixed UL per the framework)
    ("UI1", "UI/UX Engineer", 1, 1),
    ("UI2", "UI/UX Engineer", 2, 2),
    ("UI3", "UI/UX Engineer", 3, 3),
    ("UL4", "UI/UX Engineer", 4, 4),
    ("UL5", "UI/UX Engineer", 5, 5),
]

TIER_NET_BAM: dict[int, Decimal] = {
    1: Decimal("1000.00"),  # legal minimum
    2: Decimal("1500.00"),
    3: Decimal("2200.00"),
    4: Decimal("3000.00"),
    5: Decimal("4000.00"),
}

BENEFITS: list[dict] = [
    {
        "benefit_type": "meal",
        "name": "Topli obrok",
        "monthly_amount": Decimal("213.00"),
    },
    {
        "benefit_type": "transport",
        "name": "Prijevoz",
        "monthly_amount": Decimal("51.00"),
    },
]


class Command(BaseCommand):
    help = "Seed CPF levels, roles, compensation policies, and benefits catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without writing.",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        dry = bool(opts.get("dry_run"))

        Role = apps.get_model("core", "Role")
        CPFLevel = apps.get_model("core", "CPFLevel")
        CompensationPolicy = self._try_get_model(
            "CompensationPolicy",
            "CompensationPolicy model not found — backend prompt not yet "
            "applied. Skipping policy + benefit seeding. Re-run after "
            "policies/benefits migrations exist.",
        )
        BenefitCatalog = self._try_get_model("BenefitCatalog", None)

        self._seed_roles(Role, dry)
        self._seed_cpf_levels(Role, CPFLevel, dry)

        if CompensationPolicy is not None:
            self._seed_policies(CompensationPolicy, dry)
        if BenefitCatalog is not None:
            self._seed_benefits(BenefitCatalog, dry)

        if dry:
            self.stdout.write(self.style.WARNING("Dry run — rolling back."))
            transaction.set_rollback(True)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _try_get_model(self, name: str, missing_msg: str | None):
        try:
            return apps.get_model("core", name)
        except LookupError:
            if missing_msg:
                self.stdout.write(self.style.WARNING(missing_msg))
            return None

    def _seed_roles(self, Role, dry: bool):
        created = updated = 0
        for name, description in ROLES:
            role, was_created = Role.objects.get_or_create(
                name=name,
                defaults={"description": description},
            )
            if was_created:
                created += 1
                continue
            if role.description != description:
                role.description = description
                if not dry:
                    role.save(update_fields=["description"])
                updated += 1
        self.stdout.write(
            self.style.SUCCESS(f"Roles: {created} created, {updated} updated.")
        )

    def _seed_cpf_levels(self, Role, CPFLevel, dry: bool):
        created = updated = 0
        for level_name, role_name, _tier, order in CPF_LEVELS:
            role = Role.objects.get(name=role_name)
            level, was_created = CPFLevel.objects.get_or_create(
                name=level_name,
                defaults={"role": role, "order": order},
            )
            if was_created:
                created += 1
                continue
            changed = False
            if level.role_id != role.id:
                level.role = role
                changed = True
            if level.order != order:
                level.order = order
                changed = True
            if changed:
                if not dry:
                    level.save(update_fields=["role", "order"])
                updated += 1
        self.stdout.write(
            self.style.SUCCESS(f"CPF levels: {created} created, {updated} updated.")
        )

    def _seed_policies(self, CompensationPolicy, dry: bool):
        today = date.today()
        created = updated = 0
        for level_name, _role_name, tier, _order in CPF_LEVELS:
            amount = TIER_NET_BAM[tier]
            obj, was_created = CompensationPolicy.objects.get_or_create(
                cpf_level=level_name,
                defaults={
                    "net_monthly": amount,
                    "currency": "BAM",
                    "effective_date": today,
                    "notes": "Seeded from CPF framework.",
                },
            )
            if was_created:
                created += 1
                continue
            if Decimal(obj.net_monthly) != amount:
                obj.net_monthly = amount
                if not dry:
                    obj.save(update_fields=["net_monthly"])
                updated += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Compensation policies: {created} created, {updated} updated."
            )
        )

    def _seed_benefits(self, BenefitCatalog, dry: bool):
        today = date.today()
        created = updated = 0
        for spec in BENEFITS:
            obj, was_created = BenefitCatalog.objects.get_or_create(
                name=spec["name"],
                defaults={
                    "benefit_type": spec["benefit_type"],
                    "monthly_amount": spec["monthly_amount"],
                    "currency": "BAM",
                    "is_active": True,
                    "effective_date": today,
                    "notes": "Seeded from CPF framework.",
                },
            )
            if was_created:
                created += 1
                continue
            changed = False
            if Decimal(obj.monthly_amount) != spec["monthly_amount"]:
                obj.monthly_amount = spec["monthly_amount"]
                changed = True
            if obj.benefit_type != spec["benefit_type"]:
                obj.benefit_type = spec["benefit_type"]
                changed = True
            if changed:
                if not dry:
                    obj.save(update_fields=["monthly_amount", "benefit_type"])
                updated += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Benefits catalog: {created} created, {updated} updated."
            )
        )
