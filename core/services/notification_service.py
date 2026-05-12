"""
In-app notification dispatch helpers.

Failures are swallowed and logged; never raised, so request paths cannot
crash because notification creation failed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from django.db.models.functions import Lower

from core.models import Notification, UserProfile

logger = logging.getLogger(__name__)


def _resolve_recipients(emails: Iterable[str]) -> list[UserProfile]:
    """Resolve a list of emails to UserProfile rows (matches user.email or profile.email_address)."""
    normalized = [e.strip().lower() for e in emails if e and e.strip()]
    if not normalized:
        return []

    by_user = (
        UserProfile.objects.select_related("user")
        .annotate(_email_lc=Lower("user__email"))
        .filter(_email_lc__in=normalized)
    )
    by_profile = (
        UserProfile.objects.select_related("user")
        .annotate(_email_lc=Lower("email_address"))
        .filter(_email_lc__in=normalized)
    )

    seen: dict[int, UserProfile] = {}
    for profile in list(by_user) + list(by_profile):
        seen[profile.pk] = profile
    return list(seen.values())


def create_notification(
    *,
    recipient: UserProfile,
    title: str,
    message: str = "",
    module: str = Notification.Module.GENERAL,
    type: str = Notification.Type.INFO,
    link: str = "",
    metadata: dict | None = None,
) -> Notification | None:
    try:
        return Notification.objects.create(
            recipient=recipient,
            title=title,
            message=message,
            module=module,
            type=type,
            link=link,
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.error(
            "Failed to create notification | recipient=%s title=%s error=%s",
            getattr(recipient, "pk", None),
            title,
            exc,
        )
        return None


def notify_signers_signature_requested(document, signers) -> int:
    """Create an in-app notification for every signer with a matching profile."""
    emails = [s.email for s in signers if getattr(s, "email", None)]
    recipients = _resolve_recipients(emails)
    if not recipients:
        logger.info(
            "Document %s: no matching profiles for signers — no in-app notifications created.",
            document.pk,
        )
        return 0

    title = f"Signature requested: {document.name}"
    message = (
        "You have been requested to sign a document in BloomHub. "
        "Open the Documents section to review and sign."
    )
    created = 0
    for recipient in recipients:
        if create_notification(
            recipient=recipient,
            title=title,
            message=message,
            module=Notification.Module.DOCUMENTS,
            type=Notification.Type.INFO,
            link=f"/documents/{document.pk}",
            metadata={"document_id": document.pk, "action": "signature_requested"},
        ):
            created += 1
    return created


def notify_signers_reminder(document, signers) -> int:
    emails = [s.email for s in signers if getattr(s, "email", None)]
    recipients = _resolve_recipients(emails)
    if not recipients:
        return 0

    title = f"Reminder: please sign {document.name}"
    message = "A signature is still required from you on this document."
    created = 0
    for recipient in recipients:
        if create_notification(
            recipient=recipient,
            title=title,
            message=message,
            module=Notification.Module.DOCUMENTS,
            type=Notification.Type.WARNING,
            link=f"/documents/{document.pk}",
            metadata={"document_id": document.pk, "action": "signature_reminder"},
        ):
            created += 1
    return created
