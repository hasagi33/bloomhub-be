import csv
import os

from django.core.management.base import BaseCommand, CommandError

from core.models import Role


class Command(BaseCommand):
    help = "Load roles from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file", type=str, help="Path to the CSV file containing roles"
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        if not os.path.exists(csv_file):
            raise CommandError(f'File "{csv_file}" does not exist')

        with open(csv_file, encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                name = row.get("name")
                description = row.get("description", "")
                if not name:
                    self.stdout.write(
                        self.style.WARNING(f"Skipping row with missing name: {row}")
                    )
                    continue
                role, created = Role.objects.get_or_create(
                    name=name, defaults={"description": description}
                )
                if created:
                    self.stdout.write(self.style.SUCCESS(f"Created role: {name}"))
                else:
                    self.stdout.write(f"Role {name} already exists")

        self.stdout.write(self.style.SUCCESS("Finished loading roles from CSV"))
