from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Announcement
from core.services.announcement_notification_service import (
    notify_announcement_published,
)


class Command(BaseCommand):
    help = "Dispatch notifications for due scheduled announcements."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send-email",
            action="store_true",
            help="Also send announcement emails to recipients.",
        )

    def handle(self, *args, **options):
        due = Announcement.objects.filter(
            scheduled_at__isnull=False,
            scheduled_at__lte=timezone.now(),
            notifications_sent_at__isnull=True,
        ).order_by("scheduled_at", "id")

        count = 0
        for announcement in due:
            notify_announcement_published(
                announcement,
                send_email=options["send_email"],
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Processed {count} announcement(s)."))
