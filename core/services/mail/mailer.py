"""
Generic email dispatch via Resend.

Template-agnostic. Per-module notification functions build their own
context and call `send_mail` / `send_mail_bulk`. Failures are logged and
returned as `False` — never raised — so callers in request paths never crash.
"""

import logging
from pathlib import Path

import resend
from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

SUBJECT_PREFIX = "[BloomHub]"

_LOGO_PATH = Path(settings.BASE_DIR) / "core" / "static" / "images" / "bloomteq.jpg"
_LOGO_CID = "bloomteq_logo"


def _logo_attachment() -> dict | None:
    """Build a Resend inline attachment for the logo, or None if unavailable."""
    try:
        content = list(_LOGO_PATH.read_bytes())
        return {
            "filename": "bloomteq.jpg",
            "content": content,
            "content_type": "image/jpeg",
            "content_id": _LOGO_CID,
        }
    except Exception as exc:
        logger.warning("Could not load logo for email: %s", exc)
        return None


def render_email(template: str, context: dict) -> str:
    """Render an email template under `core/templates/emails/`."""
    return render_to_string(f"emails/{template}", context)


def send_mail(
    *,
    to: str,
    subject: str,
    html: str,
    attachments: list[dict] | None = None,
) -> bool:
    """Dispatch a single email via Resend. Returns True on success."""
    api_key = getattr(settings, "RESEND_API_KEY", "")
    from_addr = getattr(
        settings, "DEFAULT_FROM_EMAIL", "BloomHub <onboarding@resend.dev>"
    )

    if not api_key:
        logger.warning("RESEND_API_KEY not set — skipping email to %s", to)
        return False

    if not to:
        logger.warning("No recipient for subject '%s' — skipping", subject)
        return False

    params: dict = {
        "from": from_addr,
        "to": [to],
        "subject": subject,
        "html": html,
    }

    extra_attachments = list(attachments or [])
    logo = _logo_attachment()
    if logo:
        extra_attachments.append(logo)
    if extra_attachments:
        params["attachments"] = extra_attachments

    try:
        resend.api_key = api_key
        resp = resend.Emails.send(params)
        logger.info(
            "Email sent | to=%s subject='%s' id=%s", to, subject, resp.get("id", "?")
        )
        return True
    except Exception as exc:
        logger.error("Email failed | to=%s subject='%s' error=%s", to, subject, exc)
        return False


def send_mail_bulk(
    *,
    recipients: list[str],
    subject: str,
    html: str,
    attachments: list[dict] | None = None,
) -> bool:
    """Send the same email to multiple recipients. True only if all succeed."""
    if not recipients:
        logger.warning("No recipients for subject '%s' — skipping", subject)
        return False

    success = True
    for to in recipients:
        if not send_mail(to=to, subject=subject, html=html, attachments=attachments):
            success = False
    return success
