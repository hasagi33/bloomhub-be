from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from core.avatar_utils import generate_initials_avatar_png, get_initials
from core.models import UserProfile


class Command(BaseCommand):
    help = "Regenerate Jira-like initials avatars for UserProfile rows"

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Regenerate avatars for all profiles (overwrite). Default only fills missing avatars.",
        )
        parser.add_argument(
            "--profile-id",
            type=int,
            default=None,
            help="Only regenerate a single profile (requires --all to overwrite if avatar already exists).",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="Only regenerate a single user's profile (requires --all to overwrite if avatar already exists).",
        )
        parser.add_argument(
            "--random",
            action="store_true",
            help="Use a random seed per profile regeneration (color/background may change each run).",
        )

    def handle(self, *args, **options):
        regenerate_all = bool(options["all"])
        use_random = bool(options["random"])

        qs = UserProfile.objects.all().order_by("id")

        profile_id = options["profile_id"]
        user_id = options["user_id"]
        if profile_id is not None:
            qs = qs.filter(id=profile_id)
        if user_id is not None:
            qs = qs.filter(user_id=user_id)

        if not regenerate_all:
            qs = qs.filter(avatar__isnull=True)

        total = qs.count()
        if total == 0:
            self.stdout.write("No profiles to regenerate.")
            return

        self.stdout.write(f"Regenerating {total} profile avatar(s)...")

        for profile in qs.iterator():
            initials = get_initials(profile.full_name, profile.user.username)
            seed = (
                f"{profile.user_id}:{profile.user.username}"
                if not use_random
                else f"{profile.user_id}:{profile.user.username}:{uuid4().hex}"
            )
            png_bytes = generate_initials_avatar_png(initials, seed=seed)

            # Overwrite the avatar file for stable URLs/keys.
            if profile.avatar:
                try:
                    profile.avatar.delete(save=False)
                except Exception:
                    pass

            profile.avatar.save(
                "avatar.png",
                ContentFile(png_bytes),
                save=True,
            )

        self.stdout.write(self.style.SUCCESS("Done."))
