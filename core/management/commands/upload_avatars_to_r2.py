from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from core.models import UserProfile


class Command(BaseCommand):
    help = "Upload locally generated UserProfile avatars to Cloudflare R2"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Upload avatars for all profiles.",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Upload avatar for a single user's profile.",
        )
        parser.add_argument(
            "--delete-local",
            action="store_true",
            help="Delete local media/avatars/* after successful upload.",
        )

    def handle(self, *args, **options):
        if not options["all"] and options["user_id"] is None:
            raise ValueError("Use --all or --user-id")

        qs = UserProfile.objects.all()
        if options["user_id"] is not None:
            qs = qs.filter(user_id=options["user_id"])
        elif options["all"]:
            qs = qs

        local_media_root = Path(settings.MEDIA_ROOT)

        uploaded = 0
        delete_local = bool(options.get("delete_local", False))

        for profile in qs.iterator():
            if not profile.avatar or not profile.avatar.name:
                continue

            # When USE_R2 is enabled (ENVIRONMENT=prod), storage is R2.
            # But the bytes we upload still come from the local MEDIA_ROOT.
            local_path = local_media_root / profile.avatar.name
            if not local_path.exists():
                self.stdout.write(
                    f"Skipping user_id={profile.user_id}: local file missing: {local_path}"
                )
                continue

            with open(local_path, "rb") as f:
                raw = f.read()

            # Save bytes into the configured storage backend (R2 in prod).
            storage = profile.avatar.storage
            content = ContentFile(raw)
            uploaded_name = storage.save(profile.avatar.name, content)

            if uploaded_name:
                uploaded += 1
                self.stdout.write(
                    f"Uploaded user_id={profile.user_id} -> {uploaded_name}"
                )

                if delete_local:
                    try:
                        os.remove(local_path)
                    except OSError:
                        pass

        self.stdout.write(self.style.SUCCESS(f"Uploaded {uploaded} avatar(s)."))
