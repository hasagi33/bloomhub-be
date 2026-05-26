"""Assign CPF levels to existing UserProfiles based on their role name.

Heuristics:
  - Role name maps to a CPF role group (FS, ME, DO, QA, UI, AI, DE, PM, SA, TL).
  - Tier inferred from qualifiers in the role name:
      "Junior"             → tier 1
      "Senior"             → tier 3
      "Lead"               → tier 4
      "Principal" / "Head" → tier 5
      otherwise            → tier 2
  - Tier clamped to the max tier available for that role group.
  - QA leads use prefix "QL", UI/UX leads use prefix "UL" (matches the
    framework). Engineering tiers stay on the base prefix.
  - Profiles without a matching role keep their existing cpf_level
    (or stay empty).

Usage:
    python manage.py assign_cpf_levels
    python manage.py assign_cpf_levels --dry-run
    python manage.py assign_cpf_levels --force   # overwrite existing values
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

# CPF role group → (max tier, lead prefix override or None)
GROUPS: dict[str, tuple[int, str | None]] = {
    "AI": (3, None),
    "DE": (3, None),
    "ME": (3, None),
    "FS": (4, None),
    "TL": (5, None),  # Tech Lead — only TL4, TL5 exist; clamp lower tiers
    "SA": (5, None),  # Solution Architect — only SA4, SA5 exist
    "DO": (5, None),
    "QA": (5, "QL"),  # tiers 4 & 5 use QL4 / QL5
    "PM": (4, None),
    "UI": (5, "UL"),  # tiers 4 & 5 use UL4 / UL5
}

# Substring (lowercased) → CPF role group. First match wins.
ROLE_PATTERNS: list[tuple[str, str]] = [
    ("frontend", "FS"),
    ("backend", "FS"),
    ("full stack", "FS"),
    ("full-stack", "FS"),
    ("fullstack", "FS"),
    ("web tech lead", "TL"),
    ("tech lead", "TL"),
    ("solution architect", "SA"),
    ("architect", "SA"),
    ("ios", "ME"),
    ("android", "ME"),
    ("mobile", "ME"),
    ("devops", "DO"),
    ("sre", "DO"),
    ("site reliability", "DO"),
    ("infrastructure", "DO"),
    ("qa ", "QA"),
    ("quality", "QA"),
    ("test engineer", "QA"),
    ("designer", "UI"),
    ("ui/ux", "UI"),
    ("ui ", "UI"),
    ("ux ", "UI"),
    ("brand", "UI"),
    ("ai engineer", "AI"),
    ("machine learning", "AI"),
    ("ml engineer", "AI"),
    ("data engineer", "DE"),
    ("data analyst", "DE"),
    ("project manager", "PM"),
    ("product manager", "PM"),
    ("delivery manager", "PM"),
]


def infer_tier(role_name: str, max_tier: int, min_tier: int = 1) -> int:
    s = role_name.lower()
    if "principal" in s or "head of" in s:
        tier = 5
    elif "lead" in s:
        tier = 4
    elif "senior" in s:
        tier = 3
    elif "junior" in s or "intern" in s:
        tier = 1
    else:
        tier = 2
    # Clamp into [min_tier, max_tier]. TL/SA have min_tier 4.
    return max(min_tier, min(tier, max_tier))


def cpf_code(group: str, tier: int) -> str | None:
    if group not in GROUPS:
        return None
    max_tier, lead_prefix = GROUPS[group]
    # Special-case groups whose lowest tier is 4 (TL, SA).
    if group in ("TL", "SA"):
        tier = max(4, tier)
    prefix = group
    if lead_prefix and tier >= 4:
        prefix = lead_prefix
    return f"{prefix}{tier}"


def resolve_group(role_name: str) -> str | None:
    s = role_name.lower()
    for needle, group in ROLE_PATTERNS:
        if needle in s:
            return group
    return None


class Command(BaseCommand):
    help = "Assign CPF levels to existing UserProfiles based on role name."

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

        assigned = skipped_existing = unmatched = invalid = 0
        details: list[str] = []

        for profile in UserProfile.objects.select_related("role").all():
            existing = (profile.cpf_level or "").strip()
            if existing and not force:
                skipped_existing += 1
                continue

            role_name = profile.role.name if profile.role else ""
            if not role_name:
                unmatched += 1
                continue

            group = resolve_group(role_name)
            if group is None:
                unmatched += 1
                continue

            max_tier, _ = GROUPS[group]
            min_tier = 4 if group in ("TL", "SA") else 1
            tier = infer_tier(role_name, max_tier=max_tier, min_tier=min_tier)
            code = cpf_code(group, tier)
            if code is None or code not in valid_levels:
                invalid += 1
                details.append(f"  invalid code {code!r} for role {role_name!r}")
                continue

            full_name = profile.full_name or (
                profile.user.username if profile.user_id else "?"
            )
            details.append(f"  {full_name} ({role_name}) → {code}")
            if not dry:
                profile.cpf_level = code
                profile.save(update_fields=["cpf_level"])
            assigned += 1

        if details:
            self.stdout.write("\n".join(details))
        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned: {assigned}  "
                f"Skipped (existing): {skipped_existing}  "
                f"Unmatched role: {unmatched}  "
                f"Invalid code: {invalid}"
            )
        )

        if dry:
            self.stdout.write(self.style.WARNING("Dry run — rolling back."))
            transaction.set_rollback(True)
