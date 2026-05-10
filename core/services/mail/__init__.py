"""
Mail package — modular notification services.

Generic primitives (`mailer`, `recipients`) are reusable across modules.
Per-domain notification functions live in `<domain>_notifications.py`
(e.g. `leave_notifications`, `document_notifications`, `auth_notifications`).
"""

from core.services.mail.mailer import render_email, send_mail, send_mail_bulk
from core.services.mail.recipients import (
    display_name,
    first_name,
    hr_recipient_emails,
    profile_email,
)

__all__ = [
    "render_email",
    "send_mail",
    "send_mail_bulk",
    "profile_email",
    "display_name",
    "first_name",
    "hr_recipient_emails",
]
