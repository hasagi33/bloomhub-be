"""
Recipient resolution helpers — shared across notification modules.
"""

import logging

logger = logging.getLogger(__name__)


def profile_email(profile) -> str:
    """Best available email address for a UserProfile."""
    return (profile.email_address or profile.user.email or "").strip()


def display_name(profile) -> str:
    """Best available display name for a UserProfile."""
    return (
        (profile.full_name or "").strip()
        or profile.user.get_full_name().strip()
        or profile.user.username
    )


def first_name(profile) -> str:
    name = display_name(profile)
    return name.split()[0] if name else ""


def hr_recipient_emails() -> list[str]:
    """
    Emails for active HR users, falling back to active staff users
    when no HR role is configured. Returns [] when neither is available.
    """
    from core.models import UserProfile

    hr_profiles = UserProfile.objects.filter(
        role__name__iexact="HR",
        is_active=True,
    ).select_related("user", "role")

    if hr_profiles.exists():
        return [email for p in hr_profiles if (email := profile_email(p))]

    from django.contrib.auth import get_user_model

    User = get_user_model()
    staff_users = User.objects.filter(is_staff=True, is_active=True)
    emails = [u.email for u in staff_users if u.email]
    if not emails:
        logger.warning("No HR or staff users available for HR notification")
    return emails
