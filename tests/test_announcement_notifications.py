from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import (
    Announcement,
    DiscordAnnouncementChannel,
    DiscordAnnouncementDelivery,
    Notification,
    Permission,
    Role,
    UserProfile,
)


class _DiscordResponse:
    def __init__(self, status_code=200, payload=None, text="OK"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


def _grant(profile: UserProfile, *actions: str) -> None:
    role = profile.role
    if role is None:
        role = Role.objects.create(name=f"announcement-notify-role-{profile.pk}")
        profile.role = role
        profile.save(update_fields=["role"])

    for action in actions:
        permission, _ = Permission.objects.get_or_create(
            module_name="Announcements",
            feature_action=action,
        )
        role.permissions.add(permission)


def _role(profile: UserProfile, name: str) -> None:
    role, _ = Role.objects.get_or_create(name=name)
    profile.role = role
    profile.save(update_fields=["role"])


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _discord_channel(
    announcement_type=Announcement.Type.NEWS,
    *,
    enabled=True,
) -> DiscordAnnouncementChannel:
    channel = DiscordAnnouncementChannel.objects.create(
        announcement_type=announcement_type,
        channel_name=f"{announcement_type}-discord",
        enabled=enabled,
    )
    channel.set_webhook_url("https://discord.com/api/webhooks/123/token")
    channel.save(update_fields=["webhook_url_encrypted", "updated_at"])
    return channel


@pytest.mark.django_db
def test_published_announcement_creates_in_app_notifications_for_readers():
    creator = User.objects.create_user(username="creator", password="x")
    reader = User.objects.create_user(username="reader", password="x")
    _role(creator.profile, "HR")
    _grant(reader.profile, "view_announcements")

    response = _client(creator).post(
        "/api/announcements/",
        {"title": "Published", "body": "<p>Hello</p>", "type": "news"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    announcement = Announcement.objects.get()
    notification = Notification.objects.get()
    assert notification.recipient == reader.profile
    assert notification.module == Notification.Module.ANNOUNCEMENTS
    assert notification.title == "New announcement: Published"
    assert notification.link == f"/announcements/{announcement.id}"
    assert notification.metadata == {
        "announcement_id": announcement.id,
        "action": "announcement_published",
    }
    announcement.refresh_from_db()
    assert announcement.notifications_sent_count == 1
    assert announcement.notifications_sent_at is not None


@pytest.mark.django_db
@override_settings(FRONTEND_URL="https://bloomhub.test")
def test_published_announcement_posts_to_matching_enabled_discord_channel(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.services.discord_announcement_service.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or _DiscordResponse(payload={"id": "discord-message-1"}),
    )
    _discord_channel(Announcement.Type.NEWS)
    _discord_channel(Announcement.Type.URGENT)
    creator = User.objects.create_user(username="creator-discord", password="x")
    _role(creator.profile, "HR")

    response = _client(creator).post(
        "/api/announcements/",
        {
            "title": "Discord News",
            "body": (
                "<p><strong>Bold</strong></p>"
                "<h2>aaaheadingaa</h2>"
                "<p>aaaheadingaa</p>"
                "<p><u><em>underlienitalic</em></u></p>"
            ),
            "type": "news",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "https://discord.com/api/webhooks/123/token"
    assert kwargs["params"] == {"wait": "true"}
    embed = kwargs["json"]["embeds"][0]
    assert embed["title"] == "Discord News"
    assert (
        embed["description"]
        == "**Bold**\n\n## aaaheadingaa\n\naaaheadingaa\n\n__*underlienitalic*__"
    )
    assert embed["url"].endswith(f"/announcements/{Announcement.objects.get().id}")
    delivery = DiscordAnnouncementDelivery.objects.get()
    assert delivery.status == DiscordAnnouncementDelivery.Status.SENT
    assert delivery.discord_message_id == "discord-message-1"


@pytest.mark.django_db
def test_discord_api_failure_records_delivery_and_publish_still_succeeds(monkeypatch):
    monkeypatch.setattr(
        "core.services.discord_announcement_service.requests.post",
        lambda *args, **kwargs: _DiscordResponse(status_code=500, text="boom"),
    )
    _discord_channel(Announcement.Type.NEWS)
    creator = User.objects.create_user(username="creator-discord-fail", password="x")
    _role(creator.profile, "HR")

    response = _client(creator).post(
        "/api/announcements/",
        {"title": "Discord Fails", "body": "<p>Hello</p>", "type": "news"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    announcement = Announcement.objects.get()
    assert announcement.notifications_sent_at is not None
    delivery = DiscordAnnouncementDelivery.objects.get()
    assert delivery.status == DiscordAnnouncementDelivery.Status.FAILED
    assert delivery.attempt_count == 1
    assert "HTTP 500" in delivery.last_error


@pytest.mark.django_db
def test_retry_discord_command_skips_sent_deliveries(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.services.discord_announcement_service.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or _DiscordResponse(payload={"id": "retry-message"}),
    )
    creator = User.objects.create_user(username="retry-discord", password="x")
    announcement = Announcement.objects.create(
        title="Retry",
        body="<p>Retry</p>",
        author=creator.profile,
        type=Announcement.Type.NEWS,
    )
    sent_channel = _discord_channel(Announcement.Type.NEWS)
    failed_channel = _discord_channel(Announcement.Type.NEWS)
    DiscordAnnouncementDelivery.objects.create(
        announcement=announcement,
        discord_channel=sent_channel,
        status=DiscordAnnouncementDelivery.Status.SENT,
        attempt_count=1,
        discord_message_id="already-sent",
        sent_at=timezone.now(),
    )
    failed_delivery = DiscordAnnouncementDelivery.objects.create(
        announcement=announcement,
        discord_channel=failed_channel,
        status=DiscordAnnouncementDelivery.Status.FAILED,
        attempt_count=1,
    )

    call_command("retry_discord_announcement_deliveries")

    assert len(calls) == 1
    failed_delivery.refresh_from_db()
    assert failed_delivery.status == DiscordAnnouncementDelivery.Status.SENT
    assert failed_delivery.attempt_count == 2


@pytest.mark.django_db
def test_announcement_email_notifications_are_opt_in(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "core.services.mail.announcement_notifications.send_mail_bulk",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    creator = User.objects.create_user(username="creator", password="x")
    reader = User.objects.create_user(
        username="reader", email="reader@test.com", password="x"
    )
    reader.profile.email_address = "reader@test.com"
    reader.profile.save(update_fields=["email_address"])
    _role(creator.profile, "HR")
    _grant(reader.profile, "view_announcements")

    response = _client(creator).post(
        "/api/announcements/",
        {
            "title": "Email me",
            "body": "<p>Hello</p>",
            "send_email_notifications": True,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert sent
    assert sent[0]["recipients"] == ["reader@test.com"]
    assert sent[0]["subject"].endswith("New announcement: Email me")
    announcement = Announcement.objects.get()
    assert announcement.email_notifications_sent_count == 1
    assert announcement.email_notifications_sent_at is not None


@pytest.mark.django_db
def test_announcement_email_notifications_include_regular_employee_without_permission(
    monkeypatch, capsys
):
    sent = []
    monkeypatch.setattr(
        "core.services.mail.announcement_notifications.send_mail_bulk",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    creator = User.objects.create_user(username="creator", password="x")
    reader = User.objects.create_user(
        username="normaluser",
        email="normaluser@mail.com",
        password="x",
    )
    reader.profile.email_address = "normaluser@mail.com"
    reader.profile.save(update_fields=["email_address"])
    _role(creator.profile, "HR")
    _role(reader.profile, "Employee")

    response = _client(creator).post(
        "/api/announcements/",
        {
            "title": "Email regular employee",
            "body": "<p>Hello regular employee</p>",
            "send_email_notifications": True,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert sent
    assert sent[0]["recipients"] == ["normaluser@mail.com"]
    announcement = Announcement.objects.get()
    assert announcement.notifications_sent_count == 1
    assert announcement.email_notifications_sent_count == 1
    output = capsys.readouterr().out
    assert "[announcements] notifying announcement" in output
    assert "[announcements] email recipients" in output
    assert "[announcements] email send result" in output
    assert "[announcements] notification complete" in output


@pytest.mark.django_db
def test_announcement_email_notifications_do_not_send_when_flag_false(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "core.services.mail.announcement_notifications.send_mail_bulk",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    creator = User.objects.create_user(username="creator", password="x")
    reader = User.objects.create_user(
        username="reader", email="reader@test.com", password="x"
    )
    reader.profile.email_address = "reader@test.com"
    reader.profile.save(update_fields=["email_address"])
    _role(creator.profile, "HR")
    _role(reader.profile, "Employee")

    response = _client(creator).post(
        "/api/announcements/",
        {
            "title": "No email",
            "body": "<p>Hello</p>",
            "send_email_notifications": False,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert sent == []
    announcement = Announcement.objects.get()
    assert announcement.notifications_sent_count == 1
    assert announcement.email_notifications_sent_count == 0
    assert announcement.email_notifications_sent_at is None


@pytest.mark.django_db
@override_settings(FRONTEND_URL="https://bloomhub.test")
def test_announcement_email_notification_renders_template(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "core.services.mail.announcement_notifications.send_mail_bulk",
        lambda **kwargs: sent.append(kwargs) or True,
    )
    author = User.objects.create_user(username="author", password="x")
    recipient = User.objects.create_user(
        username="recipient", email="reader@test.com", password="x"
    )
    recipient.profile.email_address = "reader@test.com"
    recipient.profile.save(update_fields=["email_address"])

    announcement = Announcement.objects.create(
        title="Template check",
        body="<p>Rendered body</p>",
        author=author.profile,
        type=Announcement.Type.NEWS,
    )

    from core.services.mail.announcement_notifications import (
        notify_announcement_published,
    )

    assert notify_announcement_published(announcement, [recipient.profile]) is True
    assert sent
    html = sent[0]["html"]
    assert "Template check" in html
    assert "Rendered body" in html
    assert "https://bloomhub.test/announcements/" in html
    assert "author" in html.lower()


@pytest.mark.django_db
def test_future_scheduled_announcement_does_not_notify_until_due():
    creator = User.objects.create_user(username="creator", password="x")
    reader = User.objects.create_user(username="reader", password="x")
    _role(creator.profile, "HR")
    _grant(creator.profile, "schedule_announcements")
    _grant(reader.profile, "view_announcements")
    scheduled_at = timezone.now() + timedelta(days=1)

    response = _client(creator).post(
        "/api/announcements/",
        {
            "title": "Later",
            "body": "<p>Later</p>",
            "scheduled_at": scheduled_at.isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Notification.objects.count() == 0
    announcement = Announcement.objects.get()
    assert announcement.notifications_sent_at is None


@pytest.mark.django_db
def test_due_scheduled_announcement_command_dispatches_once():
    creator = User.objects.create_user(username="creator", password="x")
    reader = User.objects.create_user(username="reader", password="x")
    _grant(reader.profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Due",
        body="<p>Due</p>",
        author=creator.profile,
        scheduled_at=timezone.now() - timedelta(minutes=1),
        published_at=timezone.now() - timedelta(minutes=1),
    )

    call_command("notify_due_announcements")
    call_command("notify_due_announcements")

    assert Notification.objects.count() == 1
    notification = Notification.objects.get()
    assert notification.recipient == reader.profile
    announcement.refresh_from_db()
    assert announcement.notifications_sent_count == 1


@pytest.mark.django_db
def test_scheduled_announcement_posts_to_discord_only_when_due(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "core.services.discord_announcement_service.requests.post",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or _DiscordResponse(payload={"id": "scheduled-message"}),
    )
    _discord_channel(Announcement.Type.NEWS)
    creator = User.objects.create_user(username="scheduled-discord", password="x")
    future_at = timezone.now() + timedelta(days=1)
    due_at = timezone.now() - timedelta(minutes=1)
    Announcement.objects.create(
        title="Future Discord",
        body="<p>Future</p>",
        author=creator.profile,
        type=Announcement.Type.NEWS,
        scheduled_at=future_at,
        published_at=future_at,
    )
    due = Announcement.objects.create(
        title="Due Discord",
        body="<p>Due</p>",
        author=creator.profile,
        type=Announcement.Type.NEWS,
        scheduled_at=due_at,
        published_at=due_at,
    )

    call_command("notify_due_announcements")

    assert len(calls) == 1
    delivery = DiscordAnnouncementDelivery.objects.get()
    assert delivery.announcement == due
    assert delivery.status == DiscordAnnouncementDelivery.Status.SENT
