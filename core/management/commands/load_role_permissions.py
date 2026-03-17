import csv
import os

from django.core.management.base import BaseCommand, CommandError

from core.models import Permission, Role


class Command(BaseCommand):
    help = "Load role permissions from a CSV file"

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to the CSV file containing role permissions",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        if not os.path.exists(csv_file):
            raise CommandError(f'File "{csv_file}" does not exist')

        with open(csv_file, encoding="utf-8") as file:
            reader = csv.DictReader(file)
            roles_operations = {}
            for row in reader:
                role_id = row.get("role_id")
                module_name = row.get("module_name")
                feature_action = row.get("feature_action")
                permission_str = row.get("permission")
                operation_type = row.get("operation_type", "override").lower()

                if (
                    not role_id
                    or not module_name
                    or not feature_action
                    or not permission_str
                ):
                    self.stdout.write(
                        self.style.WARNING(f"Skipping row with missing data: {row}")
                    )
                    continue

                # Get or create permission
                permission, created = Permission.objects.get_or_create(
                    module_name=module_name, feature_action=feature_action
                )
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f"Created permission: {permission}")
                    )

                # Get or create role
                role, created = Role.objects.get_or_create(
                    name=role_id, defaults={"description": f"Role {role_id}"}
                )
                if created:
                    self.stdout.write(self.style.SUCCESS(f"Created role: {role_id}"))

                # Initialize operations for role
                if role_id not in roles_operations:
                    roles_operations[role_id] = {
                        "override": set(),
                        "add": set(),
                        "remove": set(),
                        "merge": {},
                    }

                ops = roles_operations[role_id]
                desired = permission_str.upper() == "YES"

                if operation_type == "override" and desired:
                    ops["override"].add(permission)
                elif operation_type == "add" and desired:
                    ops["add"].add(permission)
                elif operation_type == "remove" and desired:
                    ops["remove"].add(permission)
                elif operation_type == "merge":
                    ops["merge"][permission] = desired

            # Now apply operations to roles
            for role_id, ops in roles_operations.items():
                role = Role.objects.get(name=role_id)

                if ops["override"]:
                    role.permissions.set(ops["override"])
                    self.stdout.write(
                        self.style.SUCCESS(f"Overrode permissions for role: {role_id}")
                    )
                else:
                    applied = False
                    if ops["add"]:
                        role.permissions.add(*ops["add"])
                        applied = True
                    if ops["remove"]:
                        role.permissions.remove(*ops["remove"])
                        applied = True
                    if ops["merge"]:
                        current = set(role.permissions.all())
                        for perm, desired in ops["merge"].items():
                            if desired:
                                current.add(perm)
                            else:
                                current.discard(perm)
                        role.permissions.set(current)
                        applied = True
                    if applied:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"Updated permissions for role: {role_id}"
                            )
                        )

        self.stdout.write(
            self.style.SUCCESS("Finished loading role permissions from CSV")
        )
