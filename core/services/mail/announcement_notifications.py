"""Announcement email notifications."""

import logging

from core.services.mail.mailer import SUBJECT_PREFIX, send_mail_bulk
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
    html = (
        f"<p>A new announcement has been published by {author}.</p>"
        f"<p><strong>{announcement.title}</strong></p>"
        f"<p>Open BloomHub to read the full announcement.</p>"
    )
    subject = f"{SUBJECT_PREFIX} New announcement: {announcement.title}"
    sent = send_mail_bulk(recipients=emails, subject=subject, html=html)
    print("[announcements] email send result " f"id={announcement.pk} sent={sent}")
    return sent
