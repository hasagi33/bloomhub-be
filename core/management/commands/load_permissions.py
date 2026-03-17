import csv
import os

from django.core.management.base import BaseCommand, CommandError

from core.models import Permission


class Command(BaseCommand):
    help = "Load permissions from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file", type=str, help="Path to the CSV file containing permissions"
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        if not os.path.exists(csv_file):
            raise CommandError(f'File "{csv_file}" does not exist')

        with open(csv_file, encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                module_name = row.get("module_name")
                feature_action = row.get("feature_action")
                if not module_name or not feature_action:
                    self.stdout.write(
                        self.style.WARNING(f"Skipping row with missing data: {row}")
                    )
                    continue
                permission, created = Permission.objects.get_or_create(
                    module_name=module_name, feature_action=feature_action
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f"Created permission: {permission}")
                    )
                else:
                    self.stdout.write(f"Permission {permission} already exists")

        self.stdout.write(self.style.SUCCESS("Finished loading permissions from CSV"))
