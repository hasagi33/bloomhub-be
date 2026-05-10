"""
Leave-request notifications — vacation approval workflow emails.
"""

import logging

from core.services.mail.mailer import (
    SUBJECT_PREFIX,
    render_email,
    send_mail,
    send_mail_bulk,
)
from core.services.mail.recipients import (
    display_name,
    hr_recipient_emails,
    profile_email,
)

logger = logging.getLogger(__name__)


def notify_lead_new_request(leave_request) -> bool:
    """Step 1 — employee submitted a request; notify all Tech Leads (managers)."""
    managers = leave_request.employee.managers.select_related("user").all()
    if not managers.exists():
        logger.warning(
            "LeaveRequest %s: employee has no managers — skipping lead notification",
            leave_request.id,
        )
        return False

    html = render_email("leave_submitted_to_lead.html", {"leave": leave_request})
    subject = (
        f"{SUBJECT_PREFIX} Vacation Request — {display_name(leave_request.employee)} "
        f"({leave_request.start_date} to {leave_request.end_date})"
    )
    return send_mail_bulk(
        recipients=[profile_email(m) for m in managers],
        subject=subject,
        html=html,
    )


def notify_hr_lead_approved(leave_request) -> bool:
    """Step 2 — Tech Lead approved; forward to all active HR users."""
    emails = hr_recipient_emails()
    if not emails:
        logger.warning(
            "LeaveRequest %s: no HR recipients — skipping HR notification",
            leave_request.id,
        )
        return False

    html = render_email("lead_approved_to_hr.html", {"leave": leave_request})
    subject = (
        f"{SUBJECT_PREFIX} Lead Approved — vacation request from "
        f"{display_name(leave_request.employee)} awaiting your review"
    )
    return send_mail_bulk(recipients=emails, subject=subject, html=html)


def notify_employee_lead_decision(leave_request, *, approved: bool) -> bool:
    """Step 2b — employee receives Tech Lead's decision."""
    html = render_email(
        "lead_decision_to_employee.html",
        {"leave": leave_request, "approved": approved},
    )
    verb = "Approved by Lead" if approved else "Rejected by Lead"
    subject = (
        f"{SUBJECT_PREFIX} Vacation Request {verb} — "
        f"{leave_request.leave_type.capitalize()} leave"
    )
    return send_mail(
        to=profile_email(leave_request.employee),
        subject=subject,
        html=html,
    )


def notify_employee_hr_decision(leave_request, *, approved: bool) -> bool:
    """Step 3 — employee receives HR's final decision."""
    html = render_email(
        "hr_decision_to_employee.html",
        {"leave": leave_request, "approved": approved},
    )
    verb = "Approved" if approved else "Rejected"
    subject = (
        f"{SUBJECT_PREFIX} Vacation Request — Final Decision: {verb} "
        f"({leave_request.leave_type.capitalize()} leave)"
    )
    return send_mail(
        to=profile_email(leave_request.employee),
        subject=subject,
        html=html,
    )


def notify_approver_confirmation(
    leave_request, approver_profile, *, approved: bool, stage: str
) -> bool:
    """Confirmation back to the approver. `stage` is 'lead' or 'hr'."""
    html = render_email(
        "approver_confirmation.html",
        {
            "leave": leave_request,
            "approver": approver_profile,
            "approved": approved,
            "stage": stage,
        },
    )
    verb = "approved" if approved else "rejected"
    subject = (
        f"{SUBJECT_PREFIX} You {verb} "
        f"{display_name(leave_request.employee)}'s vacation request"
    )
    return send_mail(
        to=profile_email(approver_profile),
        subject=subject,
        html=html,
    )
