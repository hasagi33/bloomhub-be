from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import Permission, Role, UserProfile
from core.utils import get_role_permissions_bitmap


class Command(BaseCommand):
    help = "Create/update a SUPER_ADMIN role and assign it to a user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="admin",
            help="Username to promote. Defaults to 'admin'.",
        )
        parser.add_argument(
            "--email",
            default="",
            help="Email address to use when creating the user.",
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Password to use when creating the user.",
        )
        parser.add_argument(
            "--create-user",
            action="store_true",
            help="Create the user if it does not already exist.",
        )

    def handle(self, *args, **options):
        username = options["username"]
        create_user = options["create_user"]
        User = get_user_model()

        user = User.objects.filter(username=username).first()
        if user is None:
            if not create_user:
                raise CommandError(
                    f"User '{username}' does not exist. Re-run with --create-user "
                    "or pass --username for an existing user."
                )
            if not options["password"]:
                raise CommandError("--password is required when using --create-user.")

            user = User.objects.create_superuser(
                username=username,
                email=options["email"],
                password=options["password"],
            )
            self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
        else:
            changed_fields = []
            if not user.is_staff:
                user.is_staff = True
                changed_fields.append("is_staff")
            if not user.is_superuser:
                user.is_superuser = True
                changed_fields.append("is_superuser")
            if changed_fields:
                user.save(update_fields=changed_fields)
                self.stdout.write(
                    self.style.SUCCESS(f"Updated Django admin flags for '{username}'.")
                )

        role, role_created = Role.objects.get_or_create(
            name="SUPER_ADMIN",
            defaults={"description": "Full access to all loaded permissions."},
        )
        if role_created:
            self.stdout.write(self.style.SUCCESS("Created role 'SUPER_ADMIN'."))

        permissions = Permission.objects.all()
        permission_count = permissions.count()
        if permission_count == 0:
            self.stdout.write(
                self.style.WARNING(
                    "No permissions are loaded yet. Run "
                    "`python manage.py load_permissions permissions.csv` first if "
                    "SUPER_ADMIN should include application permissions."
                )
            )
        role.permissions.set(permissions)

        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "full_name": user.get_full_name() or user.username,
                "email_address": user.email,
            },
        )
        profile.role = role
        profile.permissions = get_role_permissions_bitmap(role)
        profile.save(update_fields=["role", "permissions"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned SUPER_ADMIN to '{username}' with "
                f"{permission_count} permission(s)."
            )
        )
