"""
Email Service — Resend integration for leave approval workflow notifications.

All public functions accept a LeaveRequest instance and send to the appropriate
recipients.  Each function returns True on success, False on failure (never raises).
Failures are logged but never crash the calling request.
"""

import logging
from pathlib import Path

import resend
from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

_LOGO_PATH = Path(settings.BASE_DIR) / "core" / "static" / "images" / "bloomteq.jpg"
_LOGO_CID = "bloomteq_logo"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _profile_email(profile) -> str:
    """Return the best available email address for a UserProfile."""
    return (profile.email_address or profile.user.email or "").strip()


def _display_name(profile) -> str:
    """Return the best available display name for a UserProfile."""
    return (
        (profile.full_name or "").strip()
        or profile.user.get_full_name().strip()
        or profile.user.username
    )


def _first_name(profile) -> str:
    name = _display_name(profile)
    return name.split()[0] if name else ""


def _logo_attachment() -> dict | None:
    """Build a Resend inline attachment for the logo, or None if file is missing."""
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


def _send(*, to: str, subject: str, html: str) -> bool:
    """
    Dispatch a single email via Resend with the logo as an inline CID attachment.
    Returns True on success.  All errors are caught and logged.
    """
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

    logo = _logo_attachment()
    if logo:
        params["attachments"] = [logo]

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


def _render(template: str, ctx: dict) -> str:
    """Render an email template, injecting pre-computed name helpers into context."""
    leave = ctx.get("leave")
    names: dict = {}
    if leave:
        emp = leave.employee
        names["employee_name"] = _display_name(emp)
        names["employee_first"] = _first_name(emp)
        if leave.lead_approver:
            names["lead_approver_name"] = _display_name(leave.lead_approver)
        if leave.approver:
            names["approver_name"] = _display_name(leave.approver)

    approver = ctx.get("approver")
    if approver:
        names["approver_display_name"] = _display_name(approver)
        names["approver_first"] = _first_name(approver)

    return render_to_string(f"emails/{template}", {**names, **ctx})


# ── Notification functions ────────────────────────────────────────────────────


def notify_lead_new_request(leave_request) -> bool:
    """
    Step 1 — Employee submitted a request.
    Sends an email to every Tech Lead (manager) of the employee.
    """
    managers = leave_request.employee.managers.select_related("user").all()
    if not managers.exists():
        logger.warning(
            "LeaveRequest %s: employee has no managers — skipping lead notification",
            leave_request.id,
        )
        return False

    ctx = {"leave": leave_request}
    html = _render("leave_submitted_to_lead.html", ctx)
    subject = (
        f"[Vacation Request] {_display_name(leave_request.employee)} "
        f"— {leave_request.start_date} to {leave_request.end_date}"
    )

    success = True
    for manager in managers:
        if not _send(to=_profile_email(manager), subject=subject, html=html):
            success = False
    return success


def notify_hr_lead_approved(leave_request) -> bool:
    """
    Step 2 — Tech Lead approved.
    Sends a forwarded request email to every active HR user.
    """
    from core.models import UserProfile

    hr_profiles = UserProfile.objects.filter(
        role__name__iexact="HR",
        is_active=True,
    ).select_related("user", "role")

    if not hr_profiles.exists():
        from django.contrib.auth import get_user_model

        User = get_user_model()
        staff_users = User.objects.filter(is_staff=True, is_active=True)
        if not staff_users.exists():
            logger.warning(
                "LeaveRequest %s: no HR users found — skipping HR notification",
                leave_request.id,
            )
            return False
        emails = [u.email for u in staff_users if u.email]
    else:
        emails = [_profile_email(p) for p in hr_profiles]

    ctx = {"leave": leave_request}
    html = _render("lead_approved_to_hr.html", ctx)
    subject = (
        f"[Lead Approved] Vacation request from "
        f"{_display_name(leave_request.employee)} awaiting your review"
    )

    success = True
    for email in emails:
        if not _send(to=email, subject=subject, html=html):
            success = False
    return success


def notify_employee_lead_decision(leave_request, *, approved: bool) -> bool:
    """Step 2b — Employee receives Tech Lead's decision."""
    ctx = {"leave": leave_request, "approved": approved}
    html = _render("lead_decision_to_employee.html", ctx)
    verb = "Approved by Lead" if approved else "Rejected by Lead"
    subject = (
        f"[Vacation Request] {verb} — {leave_request.leave_type.capitalize()} leave"
    )
    return _send(to=_profile_email(leave_request.employee), subject=subject, html=html)


def notify_employee_hr_decision(leave_request, *, approved: bool) -> bool:
    """Step 3 — Employee receives HR's final decision."""
    ctx = {"leave": leave_request, "approved": approved}
    html = _render("hr_decision_to_employee.html", ctx)
    verb = "Approved" if approved else "Rejected"
    subject = f"[Vacation Request] Final Decision: {verb} — {leave_request.leave_type.capitalize()} leave"
    return _send(to=_profile_email(leave_request.employee), subject=subject, html=html)


def notify_approver_confirmation(
    leave_request, approver_profile, *, approved: bool, stage: str
) -> bool:
    """Confirmation email back to whoever just approved/rejected. stage: 'lead' or 'hr'"""
    ctx = {
        "leave": leave_request,
        "approver": approver_profile,
        "approved": approved,
        "stage": stage,
    }
    html = _render("approver_confirmation.html", ctx)
    verb = "approved" if approved else "rejected"
    subject = (
        f"[BloomHub] You {verb} "
        f"{_display_name(leave_request.employee)}'s vacation request"
    )
    return _send(to=_profile_email(approver_profile), subject=subject, html=html)
