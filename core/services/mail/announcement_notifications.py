"""Announcement email notifications."""

import logging

from django.conf import settings

from core.services.mail.mailer import SUBJECT_PREFIX, render_email, send_mail_bulk
from core.services.mail.recipients import display_name, profile_email

logger = logging.getLogger(__name__)


def notify_announcement_published(announcement, recipients) -> bool:
    emails = [email for profile in recipients if (email := profile_email(profile))]
    print(
        "[announcements] email recipients "
        f"id={announcement.pk} count={len(emails)} emails={emails}"
    )

    if not emails:
        logger.info(
            "Announcement %s: no email recipients — skipping email notification",
            announcement.pk,
        )
        return False

    author = display_name(announcement.author) if announcement.author else "BloomHub"
    site_url = (
        getattr(settings, "FRONTEND_URL", "") or getattr(settings, "SITE_URL", "") or ""
    ).rstrip("/")
    announcement_url = f"{site_url}/announcements/{announcement.pk}" if site_url else ""
    html = render_email(
        "announcement_notification.html",
        {
            "announcement": announcement,
            "author_name": author,
            "announcement_type": (
                announcement.get_type_display()
                if getattr(announcement, "type", "")
                else ""
            ),
            "announcement_url": announcement_url,
            "site_url": site_url,
        },
    )
    subject = f"{SUBJECT_PREFIX} New announcement: {announcement.title}"
    sent = send_mail_bulk(recipients=emails, subject=subject, html=html)
    print("[announcements] email send result " f"id={announcement.pk} sent={sent}")
    return sent
