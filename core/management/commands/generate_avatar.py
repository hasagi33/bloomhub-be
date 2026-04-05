"""
Generate an avatar for a specific user.

Usage:
    python manage.py generate_avatar --username hananb
    python manage.py generate_avatar --email hananb@test.com
"""

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from core.avatar_utils import generate_initials_avatar_png, get_initials


class Command(BaseCommand):
    help = "Generate an avatar for a specific user by username or email"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            help="Username of the user",
        )
        parser.add_argument(
            "--email",
            type=str,
            help="Email of the user",
        )

    def handle(self, *args, **options):
        username = options.get("username")
        email = options.get("email")

        if not username and not email:
            self.stdout.write(
                self.style.ERROR("Please provide either --username or --email")
            )
            return

        # Find the user
        try:
            if username:
                user = User.objects.get(username=username)
            else:
                user = User.objects.get(email=email)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User not found: {username or email}"))
            return

        # Get the user's profile
        try:
            profile = user.profile
        except Exception:
            self.stdout.write(
                self.style.ERROR(f"User profile not found for {user.username}")
            )
            return

        # Generate initials and avatar
        name_for_avatar = profile.full_name or user.get_full_name() or user.username
        initials = get_initials(name_for_avatar, user.username)
        seed = f"{user.id}:{user.username}"

        self.stdout.write(f"Generating avatar for {user.username} ({user.email})")
        self.stdout.write(f"  Name: {name_for_avatar}")
        self.stdout.write(f"  Initials: {initials}")

        try:
            png_bytes = generate_initials_avatar_png(initials, seed=seed)
            profile.avatar.save(
                "avatar.png",
                ContentFile(png_bytes),
                save=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Avatar generated successfully for {user.username}"
                )
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to generate avatar: {str(e)}"))
