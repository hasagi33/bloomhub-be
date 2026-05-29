from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Announcement, Notification, UserProfile
from core.permissions import can_view_announcements
from core.services.discord_announcement_service import dispatch_discord_announcement
from core.services.mail.announcement_notifications import (
    notify_announcement_published as notify_announcement_published_email,
)
from core.services.notification_service import create_notification

logger = logging.getLogger(__name__)


def announcement_is_published(announcement: Announcement) -> bool:
    return (
        announcement.scheduled_at is None or announcement.scheduled_at <= timezone.now()
    )


def announcement_notification_recipients(
    announcement: Announcement | None = None,
) -> list[UserProfile]:
    profiles = UserProfile.objects.select_related("user", "role").filter(is_active=True)
    recipients = [
        profile
        for profile in profiles
        if can_view_announcements(profile.user)
        and (announcement is None or profile.pk != announcement.author_id)
    ]
    return recipients


@transaction.atomic
def notify_announcement_published(
    announcement: Announcement,
    *,
    send_email: bool = False,
) -> dict[str, int | bool]:
    announcement = Announcement.objects.select_for_update().get(pk=announcement.pk)
    print(
        "[announcements] notifying announcement "
        f"id={announcement.pk} title={announcement.title!r} send_email={send_email}"
    )
    if announcement.notifications_sent_at:
        print(
            "[announcements] notification skipped "
            f"id={announcement.pk} reason=already_sent"
        )
        return {"in_app": 0, "email": 0, "already_sent": True}
    if not announcement_is_published(announcement):
        print(
            "[announcements] notification skipped "
            f"id={announcement.pk} reason=not_published scheduled_at={announcement.scheduled_at}"
        )
        return {"in_app": 0, "email": 0, "already_sent": False}

    recipients = announcement_notification_recipients(announcement)
    print(
        "[announcements] notification recipients "
        f"id={announcement.pk} count={len(recipients)}"
    )
    created = 0
    for recipient in recipients:
        if create_notification(
            recipient=recipient,
            title=f"New announcement: {announcement.title}",
            message="A new company announcement has been published.",
            module=Notification.Module.ANNOUNCEMENTS,
            type=Notification.Type.INFO,
            link=f"/announcements/{announcement.pk}",
            metadata={
                "announcement_id": announcement.pk,
                "action": "announcement_published",
            },
        ):
            created += 1

    email_sent = 0
    if send_email and recipients:
        print(
            "[announcements] email notification requested "
            f"id={announcement.pk} recipients={len(recipients)}"
        )
        email_sent = (
            len(recipients)
            if notify_announcement_published_email(announcement, recipients)
            else 0
        )
    elif send_email:
        print(
            "[announcements] email notification skipped "
            f"id={announcement.pk} reason=no_recipients"
        )

    announcement.notifications_sent_at = timezone.now()
    announcement.notifications_sent_count = created
    announcement.email_notifications_sent_at = timezone.now() if email_sent else None
    announcement.email_notifications_sent_count = email_sent
    announcement.save(
        update_fields=[
            "notifications_sent_at",
            "notifications_sent_count",
            "email_notifications_sent_at",
            "email_notifications_sent_count",
            "updated_at",
        ]
    )
    logger.info(
        "Announcement %s notification dispatch complete | in_app=%s email=%s",
        announcement.pk,
        created,
        email_sent,
    )
    print(
        "[announcements] notification complete "
        f"id={announcement.pk} in_app={created} email={email_sent}"
    )
    _dispatch_discord_best_effort(announcement.pk)
    return {"in_app": created, "email": email_sent, "already_sent": False}


def _dispatch_discord_best_effort(announcement_id: int) -> None:
    try:
        announcement = Announcement.objects.select_related("author__user").get(
            pk=announcement_id
        )
        dispatch_discord_announcement(announcement)
    except Exception:
        logger.exception(
            "Announcement %s Discord dispatch failed",
            announcement_id,
        )
