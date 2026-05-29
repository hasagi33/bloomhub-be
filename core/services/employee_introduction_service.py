from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.models import Announcement, AnnouncementSettings, UserProfile
from core.permissions import can_manage_announcements, can_schedule_announcements
from core.services.announcement_notification_service import (
    announcement_is_published,
    notify_announcement_published,
)

DEFAULT_EMPLOYEE_INTRO_ANNOUNCEMENT_TEMPLATE = (
    "<p>Please welcome {name} to {department}.</p>"
)


class _BlankDefaultDict(defaultdict):
    def __missing__(self, key):
        return ""


def _format_date(value: date | None) -> str:
    return value.isoformat() if value else ""


def _placeholder_values(profile: UserProfile) -> dict[str, str]:
    user = profile.user
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    name = profile.full_name or user.get_full_name() or user.username
    return {
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "department": profile.department or "",
        "role": profile.role.name if profile.role else "",
        "start_date": _format_date(profile.start_date),
    }


def render_employee_intro_template(template: str, profile: UserProfile) -> str:
    values = _BlankDefaultDict(str)
    values.update(_placeholder_values(profile))
    return template.format_map(values)


def default_employee_intro_title(profile: UserProfile) -> str:
    return render_employee_intro_template("Welcome {name}", profile)


def default_employee_intro_body(profile: UserProfile) -> str:
    template = getattr(
        settings,
        "EMPLOYEE_INTRO_ANNOUNCEMENT_TEMPLATE",
        DEFAULT_EMPLOYEE_INTRO_ANNOUNCEMENT_TEMPLATE,
    )
    return render_employee_intro_template(template, profile)


def announcement_settings() -> AnnouncementSettings:
    return AnnouncementSettings.load()


@transaction.atomic
def publish_employee_intro_announcement(
    *,
    profile: UserProfile,
    actor,
    title: str | None = None,
    body: str | None = None,
    scheduled_at=None,
    enforce_permissions: bool = True,
) -> Announcement:
    if profile.intro_announcement_id:
        raise ValidationError(
            {"publish_intro_announcement": "Introduction announcement already exists."}
        )
    if enforce_permissions and not can_manage_announcements(actor):
        raise PermissionDenied(
            "Publishing employee introductions requires an HR, lead, manager, or admin role."
        )
    if (
        enforce_permissions
        and scheduled_at
        and scheduled_at > timezone.now()
        and not can_schedule_announcements(actor)
    ):
        raise PermissionDenied(
            "Scheduling employee introductions requires schedule_announcements permission."
        )

    author = getattr(actor, "profile", None)
    if author is None:
        raise PermissionDenied("Authenticated employee profile required.")

    announcement = Announcement.objects.create(
        title=(title or default_employee_intro_title(profile)).strip(),
        body=(body or default_employee_intro_body(profile)).strip(),
        type=Announcement.Type.CELEBRATION,
        author=author,
        scheduled_at=scheduled_at,
        published_at=scheduled_at or timezone.now(),
    )
    profile.intro_announcement = announcement
    profile.intro_announcement_published_at = timezone.now()
    profile.save(
        update_fields=[
            "intro_announcement",
            "intro_announcement_published_at",
            "updated_at",
        ]
    )
    if announcement_is_published(announcement):
        notify_announcement_published(announcement)
    return announcement


def auto_publish_employee_intro_announcement(
    *,
    profile: UserProfile,
    actor,
    title: str | None = None,
    body: str | None = None,
) -> Announcement:
    return publish_employee_intro_announcement(
        profile=profile,
        actor=actor,
        title=title,
        body=body,
        enforce_permissions=False,
    )
