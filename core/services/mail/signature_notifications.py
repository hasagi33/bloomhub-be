"""
Document e-signature notifications.

Emails sent to each signer when a signature request is initiated and when
a reminder is dispatched.
"""

import logging

from django.conf import settings

from core.services.mail.mailer import (
    SUBJECT_PREFIX,
    render_email,
    send_mail,
)

logger = logging.getLogger(__name__)


def _signature_context(document, signer, requester=None) -> dict:
    return {
        "document": document,
        "signer": signer,
        "requester": requester,
        "site_url": getattr(settings, "SITE_URL", "") or "",
    }


def notify_signature_requested(document, signer, requester=None) -> bool:
    """Initial e-signature request email to a single signer."""
    if not signer.email:
        logger.warning(
            "Document %s signer %s has no email — skipping signature request mail",
            document.pk,
            signer.pk,
        )
        return False

    html = render_email(
        "signature_request.html", _signature_context(document, signer, requester)
    )
    subject = f'{SUBJECT_PREFIX} Signature requested — "{document.name}"'
    return send_mail(to=signer.email, subject=subject, html=html)


def notify_signature_reminder(document, signer, requester=None) -> bool:
    """Reminder email to a pending signer."""
    if not signer.email:
        return False

    html = render_email(
        "signature_reminder.html", _signature_context(document, signer, requester)
    )
    subject = f'{SUBJECT_PREFIX} Reminder — please sign "{document.name}"'
    return send_mail(to=signer.email, subject=subject, html=html)
