from django.core.management.base import BaseCommand

from core.services.tempo_absence_sync_service import retry_failed_syncs


class Command(BaseCommand):
    help = "Retry failed Tempo absence sync rows."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        result = retry_failed_syncs(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(f"Tempo absence retry: {result}"))
