from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Copy files from local MEDIA_ROOT into the configured default storage "
        "(Cloudflare R2 when R2 env vars are set)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete-local",
            action="store_true",
            help="Delete local files after they are copied successfully.",
        )
        parser.add_argument(
            "--prefix",
            default="",
            help=(
                "Only sync files under this MEDIA_ROOT-relative prefix, "
                "for example documents/ or employee_documents/."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be copied without writing to storage.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Delete the destination key first when it already exists.",
        )

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT).resolve()
        prefix = str(options["prefix"] or "").strip().lstrip("/")
        source_root = (media_root / prefix).resolve() if prefix else media_root

        if not source_root.exists():
            raise CommandError(f"Source path does not exist: {source_root}")
        if not source_root.is_dir():
            raise CommandError(f"Source path is not a directory: {source_root}")
        if media_root not in [source_root, *source_root.parents]:
            raise CommandError("Prefix must stay inside MEDIA_ROOT.")

        delete_local = bool(options["delete_local"])
        dry_run = bool(options["dry_run"])
        overwrite = bool(options["overwrite"])

        copied = 0
        skipped = 0
        deleted = 0

        for local_path in source_root.rglob("*"):
            if not local_path.is_file():
                continue

            key = local_path.relative_to(media_root).as_posix()
            exists = default_storage.exists(key)

            if dry_run:
                action = "overwrite" if exists and overwrite else "copy"
                if exists and not overwrite:
                    action = "skip-existing"
                self.stdout.write(f"{action}: {key}")
                continue

            if exists:
                if overwrite:
                    default_storage.delete(key)
                else:
                    skipped += 1
                    self.stdout.write(f"Skipping existing: {key}")
                    continue

            with local_path.open("rb") as fh:
                saved_key = default_storage.save(key, File(fh))
            copied += 1
            self.stdout.write(f"Copied: {key} -> {saved_key}")

            if delete_local:
                try:
                    os.remove(local_path)
                    deleted += 1
                except OSError as exc:
                    self.stderr.write(f"Could not delete {local_path}: {exc}")

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry run complete."))
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {copied} file(s), skipped {skipped}, deleted {deleted} local file(s)."
            )
        )
