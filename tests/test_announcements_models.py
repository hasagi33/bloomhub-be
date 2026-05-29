import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import (
    Announcement,
    AnnouncementReaction,
    CelebrationEvent,
    DiscordAnnouncementChannel,
    DiscordAnnouncementDelivery,
)


@pytest.mark.django_db
def test_announcement_stores_rich_text_and_author_profile():
    user = User.objects.create_user(username="author", password="x")
    author = user.profile

    announcement = Announcement.objects.create(
        title="Company update",
        body="<p><strong>Important</strong> update</p>",
        author=author,
        type=Announcement.Type.NEWS,
    )

    assert announcement.author == author
    assert announcement.body == "<p><strong>Important</strong> update</p>"
    assert announcement.published_at <= timezone.now()
    assert str(announcement) == "Company update"


@pytest.mark.django_db
def test_announcement_reaction_is_unique_per_announcement_user_and_type():
    author = User.objects.create_user(username="author", password="x").profile
    reacting_user = User.objects.create_user(username="reactor", password="x").profile
    announcement = Announcement.objects.create(
        title="Launch",
        body="<p>Launch day</p>",
        author=author,
    )

    AnnouncementReaction.objects.create(
        announcement=announcement,
        user=reacting_user,
        reaction_type="party",
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        AnnouncementReaction.objects.create(
            announcement=announcement,
            user=reacting_user,
            reaction_type="party",
        )

    AnnouncementReaction.objects.create(
        announcement=announcement,
        user=reacting_user,
        reaction_type="heart",
    )
    assert announcement.reactions.count() == 2


@pytest.mark.django_db
def test_discord_channel_encrypts_webhook_url_and_delivery_is_unique():
    author = User.objects.create_user(username="author-discord", password="x").profile
    announcement = Announcement.objects.create(
        title="Launch",
        body="<p>Launch day</p>",
        author=author,
        type=Announcement.Type.NEWS,
    )
    channel = DiscordAnnouncementChannel(
        announcement_type=Announcement.Type.NEWS,
        channel_name="news",
    )
    channel.set_webhook_url("https://discord.com/api/webhooks/123/token")
    channel.save()

    assert channel.webhook_url_encrypted != "https://discord.com/api/webhooks/123/token"
    assert channel.webhook_url_encrypted.startswith("gAAAA")
    assert channel.get_webhook_url() == "https://discord.com/api/webhooks/123/token"
    assert channel.has_webhook_url is True

    DiscordAnnouncementDelivery.objects.create(
        announcement=announcement,
        discord_channel=channel,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DiscordAnnouncementDelivery.objects.create(
            announcement=announcement,
            discord_channel=channel,
        )


@pytest.mark.django_db
def test_celebration_event_supports_stored_birthdays_anniversaries_and_custom_events():
    employee = User.objects.create_user(username="employee", password="x").profile
    creator = User.objects.create_user(username="creator", password="x").profile

    birthday = CelebrationEvent.objects.create(
        title="Employee birthday",
        event_type=CelebrationEvent.Type.BIRTHDAY,
        employee=employee,
        event_date="2026-07-14",
        created_by=creator,
    )
    anniversary = CelebrationEvent.objects.create(
        title="Work anniversary",
        event_type=CelebrationEvent.Type.ANNIVERSARY,
        employee=employee,
        event_date="2026-09-01",
    )
    custom = CelebrationEvent.objects.create(
        title="Team celebration",
        event_type=CelebrationEvent.Type.CUSTOM,
        event_date="2026-10-20",
        recurs_annually=False,
        description="Release milestone",
    )

    assert birthday.recurs_annually is True
    assert anniversary.employee == employee
    assert custom.employee is None
    assert custom.recurs_annually is False
    assert str(birthday) == "Employee birthday (2026-07-14)"
