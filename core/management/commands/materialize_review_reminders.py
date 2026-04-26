from django.core.management.base import BaseCommand

from core.services.performance_review_service import (
    materialize_performance_review_reminders,
)


class Command(BaseCommand):
    help = "Create and dispatch due in-app performance review reminders."

    def handle(self, *args, **options):
        stats = materialize_performance_review_reminders(actor=None)
        self.stdout.write(
            self.style.SUCCESS(
                "Processed {reviews} reviews, created {created} reminders, dispatched {sent} reminders.".format(
                    reviews=stats["reviews_processed"],
                    created=stats["created_count"],
                    sent=stats["sent_count"],
                )
            )
        )
