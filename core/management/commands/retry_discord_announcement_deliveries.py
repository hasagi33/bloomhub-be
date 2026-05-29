from django.core.management.base import BaseCommand

from core.models import DiscordAnnouncementDelivery
from core.services.discord_announcement_service import send_discord_delivery


class Command(BaseCommand):
    help = "Retry pending/failed Discord announcement deliveries."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-attempts",
            type=int,
            default=3,
            help="Retry deliveries with fewer than this many attempts.",
        )

    def handle(self, *args, **options):
        max_attempts = options["max_attempts"]
        deliveries = (
            DiscordAnnouncementDelivery.objects.select_related(
                "announcement__author__user",
                "discord_channel",
            )
            .filter(
                status__in=[
                    DiscordAnnouncementDelivery.Status.PENDING,
                    DiscordAnnouncementDelivery.Status.FAILED,
                ],
                attempt_count__lt=max_attempts,
            )
            .order_by("last_attempt_at", "created_at", "id")
        )

        sent = 0
        failed = 0
        for delivery in deliveries:
            if send_discord_delivery(delivery):
                sent += 1
            else:
                failed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Retried Discord deliveries: sent={sent} failed={failed}"
            )
        )
