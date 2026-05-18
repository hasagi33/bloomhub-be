from django.core.management.base import BaseCommand

from core.models import Asset
from core.services.asset_qr import (
    build_asset_qr_image_path,
    build_asset_qr_payload,
    ensure_asset_qr_code,
)


class Command(BaseCommand):
    help = "Backfill stable QR payloads and PNG images for assets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report assets that need QR data without writing files or rows.",
        )
        parser.add_argument(
            "--regenerate-images",
            action="store_true",
            help="Rewrite QR PNG images while keeping stable payloads.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        regenerate_images = options["regenerate_images"]
        updated = 0

        assets = Asset.objects.order_by("pk")
        for asset in assets:
            expected_payload = build_asset_qr_payload(asset)
            expected_image_path = build_asset_qr_image_path(asset)
            needs_qr = (
                regenerate_images
                or asset.qr_code_payload != expected_payload
                or asset.qr_code_image.name != expected_image_path
            )
            if not needs_qr:
                continue

            updated += 1
            if dry_run:
                continue

            ensure_asset_qr_code(asset, regenerate_image=regenerate_images)

        action = "would update" if dry_run else "updated"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated} asset QR codes"))
