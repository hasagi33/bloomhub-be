from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from core.models import Notification, Role
from core.services.mail import leave_notifications, mailer, recipients
from core.services.notification_service import (
    _resolve_recipients,
    create_notification,
    notify_signers_reminder,
    notify_signers_signature_requested,
)


def test_recipient_helpers():
    user = SimpleNamespace(
        email="user@example.com", get_full_name=lambda: "User Name", username="uname"
    )
    profile = SimpleNamespace(
        email_address=" profile@example.com ", full_name="  Profile Name  ", user=user
    )

    assert recipients.profile_email(profile) == "profile@example.com"
    assert recipients.display_name(profile) == "Profile Name"
    assert recipients.first_name(profile) == "Profile"


def test_mailer_logo_attachment_and_send_paths(monkeypatch):
    monkeypatch.setattr(mailer.Path, "read_bytes", lambda self: b"jpeg-bytes")
    logo = mailer._logo_attachment()
    assert logo["filename"] == "bloomteq.jpg"

    monkeypatch.setattr(mailer.settings, "RESEND_API_KEY", "", raising=False)
    assert mailer.send_mail(to="a@example.com", subject="S", html="<p>x</p>") is False
    assert mailer.send_mail(to="", subject="S", html="<p>x</p>") is False

    monkeypatch.setattr(mailer.settings, "RESEND_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(
        mailer.settings,
        "DEFAULT_FROM_EMAIL",
        "BloomHub <test@example.com>",
        raising=False,
    )

    captured = {}

    def fake_send(params):
        captured.update(params)
        return {"id": "msg-1"}

    monkeypatch.setattr(mailer.resend.Emails, "send", fake_send)
    assert (
        mailer.send_mail(
            to="a@example.com",
            subject="S",
            html="<p>x</p>",
            attachments=[{"filename": "a.txt"}],
        )
        is True
    )
    assert captured["from"] == "BloomHub <test@example.com>"
    assert captured["to"] == ["a@example.com"]
    assert len(captured["attachments"]) == 2

    monkeypatch.setattr(
        mailer.resend.Emails,
        "send",
        lambda params: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert mailer.send_mail(to="a@example.com", subject="S", html="<p>x</p>") is False
    assert mailer.send_mail_bulk(recipients=[], subject="S", html="<p>x</p>") is False
    monkeypatch.setattr(
        mailer, "send_mail", lambda **kwargs: kwargs["to"] != "bad@example.com"
    )
    assert (
        mailer.send_mail_bulk(
            recipients=["ok@example.com", "bad@example.com"],
            subject="S",
            html="<p>x</p>",
        )
        is False
    )


def test_leave_notification_helpers(monkeypatch):
    class FakeManagers:
        def __init__(self, managers):
            self._managers = managers

        def select_related(self, *args, **kwargs):
            return self

        def all(self):
            return self

        def __iter__(self):
            return iter(self._managers)

        def exists(self):
            return bool(self._managers)

    employee = SimpleNamespace(
        full_name="John Doe",
        email_address="employee@example.com",
        leave_type="vacation",
        start_date="2026-01-01",
        end_date="2026-01-05",
        managers=FakeManagers([SimpleNamespace(email_address="lead@example.com")]),
    )
    leave_request = SimpleNamespace(
        id=1,
        employee=employee,
        leave_type="vacation",
        start_date="2026-01-01",
        end_date="2026-01-05",
    )

    rendered = []
    monkeypatch.setattr(
        leave_notifications,
        "render_email",
        lambda template, context: rendered.append((template, context)) or "<html>",
    )
    sent = []
    monkeypatch.setattr(
        leave_notifications,
        "send_mail_bulk",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    monkeypatch.setattr(
        leave_notifications, "send_mail", lambda **kwargs: sent.append(kwargs) or True
    )
    monkeypatch.setattr(leave_notifications, "display_name", lambda profile: "John Doe")
    monkeypatch.setattr(
        leave_notifications, "profile_email", lambda profile: profile.email_address
    )
    monkeypatch.setattr(
        leave_notifications, "hr_recipient_emails", lambda: ["hr@example.com"]
    )

    assert leave_notifications.notify_lead_new_request(leave_request) is True
    assert leave_notifications.notify_hr_lead_approved(leave_request) is True
    assert (
        leave_notifications.notify_employee_lead_decision(leave_request, approved=True)
        is True
    )
    assert (
        leave_notifications.notify_employee_hr_decision(leave_request, approved=False)
        is True
    )
    assert (
        leave_notifications.notify_approver_confirmation(
            leave_request,
            SimpleNamespace(email_address="lead@example.com"),
            approved=True,
            stage="lead",
        )
        is True
    )
    assert rendered
    assert sent


@pytest.mark.django_db
def test_notification_service_resolution_and_creation(monkeypatch):
    user = User.objects.create_user(
        username="notify", email="notify@example.com", password="x"
    )
    other = User.objects.create_user(
        username="other", email="other@example.com", password="x"
    )
    role = Role.objects.create(name="HR")
    profile = user.profile
    profile.email_address = "profile@example.com"
    profile.role = role
    profile.save(update_fields=["email_address", "role"])
    other.profile.email_address = "other-profile@example.com"
    other.profile.save(update_fields=["email_address"])

    resolved = _resolve_recipients(
        [" notify@example.com ", "profile@example.com", "missing@example.com"]
    )
    assert {p.pk for p in resolved} == {profile.pk}

    notif = create_notification(
        recipient=profile,
        title="Hello",
        message="World",
        metadata={"a": 1},
    )
    assert notif is not None
    assert Notification.objects.count() == 1

    monkeypatch.setattr(
        Notification.objects,
        "create",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert create_notification(recipient=profile, title="Fail") is None

    created = []
    monkeypatch.setattr(
        "core.services.notification_service._resolve_recipients",
        lambda emails: [profile, other.profile],
    )
    monkeypatch.setattr(
        "core.services.notification_service.create_notification",
        lambda **kwargs: created.append(kwargs) or True,
    )

    document = SimpleNamespace(pk=11, name="Contract")
    signers = [
        SimpleNamespace(email="notify@example.com"),
        SimpleNamespace(email="other@example.com"),
    ]
    assert notify_signers_signature_requested(document, signers) == 2
    assert notify_signers_reminder(document, signers) == 2
    assert created[0]["module"] == Notification.Module.DOCUMENTS
