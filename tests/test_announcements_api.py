from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import (
    Announcement,
    AnnouncementComment,
    AnnouncementReaction,
    DiscordAnnouncementChannel,
    Permission,
    Role,
    UserProfile,
)


def _grant(profile: UserProfile, *actions: str) -> None:
    role = profile.role
    if role is None:
        role = Role.objects.create(name=f"announcement-role-{profile.pk}")
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


def _results(payload):
    if isinstance(payload, dict) and "results" in payload:
        return payload["results"]
    return payload


@pytest.mark.django_db
def test_reader_sees_due_announcements_but_not_future_scheduled_items():
    user = User.objects.create_user(username="reader", password="x")
    profile = user.profile
    _grant(profile, "view_announcements")

    due = Announcement.objects.create(
        title="Visible",
        body="<p>Visible now</p>",
        author=profile,
    )
    Announcement.objects.create(
        title="Future",
        body="<p>Not yet</p>",
        author=profile,
        scheduled_at=timezone.now() + timedelta(days=1),
        published_at=timezone.now() + timedelta(days=1),
    )

    response = _client(user).get("/api/announcements/")

    assert response.status_code == status.HTTP_200_OK
    rows = _results(response.json())
    assert [row["id"] for row in rows] == [due.id]


@pytest.mark.django_db
def test_regular_employee_can_read_due_announcements_without_seeded_permission():
    author = User.objects.create_user(username="announcement-author", password="x")
    reader = User.objects.create_user(
        username="normaluser",
        email="normaluser@mail.com",
        password="x",
    )
    _role(reader.profile, "employee")
    announcement = Announcement.objects.create(
        title="Company news",
        body="<p>Visible to everyone</p>",
        author=author.profile,
    )

    response = _client(reader).get("/api/announcements/")

    assert response.status_code == status.HTTP_200_OK
    rows = _results(response.json())
    assert [row["id"] for row in rows] == [announcement.id]


@pytest.mark.django_db
def test_reader_can_retrieve_rich_text_body_for_due_announcement():
    user = User.objects.create_user(username="reader", password="x")
    profile = user.profile
    _grant(profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Policy",
        body="<h2>Policy</h2><p><strong>Read this</strong></p>",
        author=profile,
    )

    response = _client(user).get(f"/api/announcements/{announcement.id}/")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["body"] == "<h2>Policy</h2><p><strong>Read this</strong></p>"


@pytest.mark.django_db
def test_create_requires_announcement_publisher_role():
    user = User.objects.create_user(username="employee", password="x")
    _grant(user.profile, "view_announcements")

    response = _client(user).post(
        "/api/announcements/",
        {"title": "Blocked", "body": "<p>No access</p>", "type": "general"},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert Announcement.objects.count() == 0


@pytest.mark.django_db
def test_creator_can_create_immediate_announcement():
    user = User.objects.create_user(username="creator", password="x")
    profile = user.profile
    _role(profile, "HR")

    response = _client(user).post(
        "/api/announcements/",
        {"title": "Published", "body": "<p>Hello</p>", "type": "news"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    announcement = Announcement.objects.get()
    assert announcement.author == profile
    assert announcement.scheduled_at is None
    assert announcement.published_at <= timezone.now()


@pytest.mark.django_db
def test_staff_can_configure_discord_announcement_channels_without_secret_leak():
    user = User.objects.create_user(
        username="staff-discord",
        password="x",
        is_staff=True,
    )

    response = _client(user).post(
        "/api/announcement-discord-channels/",
        {
            "announcement_type": "news",
            "channel_name": "company-news",
            "webhook_url": "https://discord.com/api/webhooks/123/token",
            "enabled": True,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["has_webhook_url"] is True
    assert "webhook_url" not in data
    channel = DiscordAnnouncementChannel.objects.get()
    assert channel.get_webhook_url() == "https://discord.com/api/webhooks/123/token"

    patch_response = _client(user).patch(
        f"/api/announcement-discord-channels/{channel.id}/",
        {"channel_name": "renamed-news"},
        format="json",
    )

    assert patch_response.status_code == status.HTTP_200_OK
    channel.refresh_from_db()
    assert channel.channel_name == "renamed-news"
    assert channel.get_webhook_url() == "https://discord.com/api/webhooks/123/token"


@pytest.mark.django_db
def test_non_staff_cannot_configure_discord_announcement_channels():
    user = User.objects.create_user(username="not-staff-discord", password="x")
    _role(user.profile, "HR")

    response = _client(user).get("/api/announcement-discord-channels/")

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_future_scheduling_requires_schedule_permission():
    user = User.objects.create_user(username="creator", password="x")
    _role(user.profile, "HR")
    scheduled_at = timezone.now() + timedelta(days=2)

    response = _client(user).post(
        "/api/announcements/",
        {
            "title": "Future",
            "body": "<p>Later</p>",
            "scheduled_at": scheduled_at.isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert Announcement.objects.count() == 0


@pytest.mark.django_db
def test_scheduler_can_create_future_announcement_and_see_it_before_due():
    user = User.objects.create_user(username="scheduler", password="x")
    profile = user.profile
    _role(profile, "HR")
    _grant(profile, "schedule_announcements")
    scheduled_at = timezone.now() + timedelta(days=3)

    response = _client(user).post(
        "/api/announcements/",
        {
            "title": "Future",
            "body": "<p>Later</p>",
            "scheduled_at": scheduled_at.isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    announcement = Announcement.objects.get()
    assert announcement.scheduled_at == scheduled_at
    assert announcement.published_at == scheduled_at

    list_response = _client(user).get("/api/announcements/")
    rows = _results(list_response.json())
    assert [row["id"] for row in rows] == [announcement.id]
    assert rows[0]["schedule_status"] == "scheduled"


@pytest.mark.django_db
def test_due_scheduled_announcement_schedule_status_is_published():
    user = User.objects.create_user(username="scheduled-reader", password="x")
    _grant(user.profile, "view_announcements")
    due_at = timezone.now() - timedelta(minutes=1)
    announcement = Announcement.objects.create(
        title="Due scheduled",
        body="<p>Due now</p>",
        author=user.profile,
        scheduled_at=due_at,
        published_at=due_at,
    )

    list_response = _client(user).get("/api/announcements/")
    detail_response = _client(user).get(f"/api/announcements/{announcement.id}/")

    assert list_response.status_code == status.HTTP_200_OK
    assert detail_response.status_code == status.HTTP_200_OK
    assert _results(list_response.json())[0]["schedule_status"] == "published"
    assert detail_response.json()["schedule_status"] == "published"


@pytest.mark.django_db
def test_clearing_future_schedule_publishes_announcement_immediately():
    user = User.objects.create_user(username="scheduler", password="x")
    profile = user.profile
    _role(profile, "HR")
    _grant(profile, "schedule_announcements")
    future = timezone.now() + timedelta(days=1)
    announcement = Announcement.objects.create(
        title="Future",
        body="<p>Later</p>",
        author=profile,
        scheduled_at=future,
        published_at=future,
    )

    response = _client(user).patch(
        f"/api/announcements/{announcement.id}/",
        {"scheduled_at": None},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    announcement.refresh_from_db()
    assert announcement.scheduled_at is None
    assert announcement.published_at <= timezone.now()


@pytest.mark.django_db
def test_creator_can_delete_announcement():
    user = User.objects.create_user(username="creator", password="x")
    profile = user.profile
    _role(profile, "DevOps Lead")
    announcement = Announcement.objects.create(
        title="Delete me",
        body="<p>Delete</p>",
        author=profile,
    )

    response = _client(user).delete(f"/api/announcements/{announcement.id}/")

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert Announcement.objects.filter(id=announcement.id).exists() is False


@pytest.mark.django_db
def test_comment_create_and_list_work_for_viewer():
    user = User.objects.create_user(username="reader-comments", password="x")
    profile = user.profile
    _grant(profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Discuss",
        body="<p>Discuss</p>",
        author=profile,
    )

    create_response = _client(user).post(
        f"/api/announcements/{announcement.id}/comments/",
        {"body": " Welcome! "},
        format="json",
    )
    list_response = _client(user).get(f"/api/announcements/{announcement.id}/comments/")

    assert create_response.status_code == status.HTTP_201_CREATED
    assert create_response.json()["body"] == "Welcome!"
    assert list_response.status_code == status.HTTP_200_OK
    assert [row["body"] for row in list_response.json()] == ["Welcome!"]


@pytest.mark.django_db
def test_empty_comment_returns_400():
    user = User.objects.create_user(username="empty-comment", password="x")
    _grant(user.profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Discuss",
        body="<p>Discuss</p>",
        author=user.profile,
    )

    response = _client(user).post(
        f"/api/announcements/{announcement.id}/comments/",
        {"body": "   "},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert AnnouncementComment.objects.count() == 0


@pytest.mark.django_db
def test_comment_delete_works_for_author():
    user = User.objects.create_user(username="comment-author", password="x")
    _grant(user.profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Discuss",
        body="<p>Discuss</p>",
        author=user.profile,
    )
    comment = AnnouncementComment.objects.create(
        announcement=announcement,
        author=user.profile,
        body="Remove me",
    )

    response = _client(user).delete(
        f"/api/announcements/{announcement.id}/comments/{comment.id}/"
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    comment.refresh_from_db()
    assert comment.deleted_at is not None


@pytest.mark.django_db
def test_comment_delete_works_for_moderator():
    author = User.objects.create_user(username="comment-writer", password="x")
    moderator = User.objects.create_user(username="comment-moderator", password="x")
    _grant(author.profile, "view_announcements")
    _grant(moderator.profile, "view_announcements", "moderate_comments")
    announcement = Announcement.objects.create(
        title="Discuss",
        body="<p>Discuss</p>",
        author=author.profile,
    )
    comment = AnnouncementComment.objects.create(
        announcement=announcement,
        author=author.profile,
        body="Moderate me",
    )

    response = _client(moderator).delete(
        f"/api/announcements/{announcement.id}/comments/{comment.id}/"
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    comment.refresh_from_db()
    assert comment.deleted_at is not None


@pytest.mark.django_db
def test_comment_delete_forbidden_for_unrelated_viewer():
    author = User.objects.create_user(username="comment-owner", password="x")
    viewer = User.objects.create_user(username="comment-viewer", password="x")
    _grant(author.profile, "view_announcements")
    _grant(viewer.profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Discuss",
        body="<p>Discuss</p>",
        author=author.profile,
    )
    comment = AnnouncementComment.objects.create(
        announcement=announcement,
        author=author.profile,
        body="Keep me",
    )

    response = _client(viewer).delete(
        f"/api/announcements/{announcement.id}/comments/{comment.id}/"
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    comment.refresh_from_db()
    assert comment.deleted_at is None


@pytest.mark.django_db
def test_deleted_comments_are_hidden_and_excluded_from_count():
    user = User.objects.create_user(username="deleted-comment", password="x")
    _grant(user.profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="Discuss",
        body="<p>Discuss</p>",
        author=user.profile,
    )
    AnnouncementComment.objects.create(
        announcement=announcement,
        author=user.profile,
        body="Visible",
    )
    AnnouncementComment.objects.create(
        announcement=announcement,
        author=user.profile,
        body="Deleted",
        deleted_at=timezone.now(),
    )

    list_response = _client(user).get(f"/api/announcements/{announcement.id}/comments/")
    detail_response = _client(user).get(f"/api/announcements/{announcement.id}/")

    assert [row["body"] for row in list_response.json()] == ["Visible"]
    assert detail_response.json()["comments_count"] == 1


@pytest.mark.django_db
def test_reaction_toggle_creates_then_removes_same_emoji_for_same_user():
    user = User.objects.create_user(username="reactor", password="x")
    _grant(user.profile, "view_announcements", "add_reactions")
    announcement = Announcement.objects.create(
        title="React",
        body="<p>React</p>",
        author=user.profile,
    )
    url = f"/api/announcements/{announcement.id}/reactions/"

    create_response = _client(user).post(
        url, {"reaction_type": " Party "}, format="json"
    )
    remove_response = _client(user).post(url, {"reaction_type": "party"}, format="json")

    assert create_response.status_code == status.HTTP_201_CREATED
    assert create_response.json()["reaction_type"] == "party"
    assert create_response.json()["active"] is True
    assert remove_response.status_code == status.HTTP_200_OK
    assert remove_response.json() == {"reaction_type": "party", "active": False}
    assert AnnouncementReaction.objects.count() == 0


@pytest.mark.django_db
def test_multiple_users_same_emoji_produce_correct_count():
    first = User.objects.create_user(username="reactor-one", password="x")
    second = User.objects.create_user(username="reactor-two", password="x")
    _grant(first.profile, "view_announcements", "add_reactions")
    _grant(second.profile, "view_announcements", "add_reactions")
    announcement = Announcement.objects.create(
        title="React",
        body="<p>React</p>",
        author=first.profile,
    )

    _client(first).post(
        f"/api/announcements/{announcement.id}/reactions/",
        {"reaction_type": "party"},
        format="json",
    )
    _client(second).post(
        f"/api/announcements/{announcement.id}/reactions/",
        {"reaction_type": "party"},
        format="json",
    )
    response = _client(first).get(f"/api/announcements/{announcement.id}/")

    assert response.json()["reaction_counts"] == {"party": 2}
    assert response.json()["my_reactions"] == ["party"]


@pytest.mark.django_db
def test_same_user_can_react_with_different_emoji_types():
    user = User.objects.create_user(username="multi-reactor", password="x")
    _grant(user.profile, "view_announcements", "add_reactions")
    announcement = Announcement.objects.create(
        title="React",
        body="<p>React</p>",
        author=user.profile,
    )
    url = f"/api/announcements/{announcement.id}/reactions/"

    _client(user).post(url, {"reaction_type": "party"}, format="json")
    _client(user).post(url, {"reaction_type": "heart"}, format="json")
    response = _client(user).get(f"/api/announcements/{announcement.id}/")

    assert response.json()["reaction_counts"] == {"heart": 1, "party": 1}
    assert response.json()["my_reactions"] == ["heart", "party"]


@pytest.mark.django_db
def test_reaction_forbidden_without_add_reactions_permission():
    user = User.objects.create_user(username="no-react", password="x")
    _grant(user.profile, "view_announcements")
    announcement = Announcement.objects.create(
        title="React",
        body="<p>React</p>",
        author=user.profile,
    )

    response = _client(user).post(
        f"/api/announcements/{announcement.id}/reactions/",
        {"reaction_type": "party"},
        format="json",
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert AnnouncementReaction.objects.count() == 0


@pytest.mark.django_db
def test_future_scheduled_announcement_engagement_forbidden_for_reader_until_due():
    scheduler = User.objects.create_user(username="future-scheduler", password="x")
    reader = User.objects.create_user(username="future-reader", password="x")
    _grant(scheduler.profile, "create_announcements", "schedule_announcements")
    _grant(reader.profile, "view_announcements", "add_reactions")
    future = timezone.now() + timedelta(days=1)
    announcement = Announcement.objects.create(
        title="Future",
        body="<p>Later</p>",
        author=scheduler.profile,
        scheduled_at=future,
        published_at=future,
    )

    comment_response = _client(reader).post(
        f"/api/announcements/{announcement.id}/comments/",
        {"body": "Too early"},
        format="json",
    )
    reaction_response = _client(reader).post(
        f"/api/announcements/{announcement.id}/reactions/",
        {"reaction_type": "party"},
        format="json",
    )

    assert comment_response.status_code == status.HTTP_404_NOT_FOUND
    assert reaction_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_announcement_list_and_detail_include_engagement_summary_fields():
    user = User.objects.create_user(username="summary-reader", password="x")
    other = User.objects.create_user(username="summary-other", password="x")
    _grant(user.profile, "view_announcements", "add_reactions")
    _grant(other.profile, "view_announcements", "add_reactions")
    announcement = Announcement.objects.create(
        title="Summary",
        body="<p>Summary</p>",
        author=user.profile,
    )
    AnnouncementReaction.objects.create(
        announcement=announcement,
        user=user.profile,
        reaction_type="heart",
    )
    AnnouncementReaction.objects.create(
        announcement=announcement,
        user=other.profile,
        reaction_type="heart",
    )
    AnnouncementComment.objects.create(
        announcement=announcement,
        author=other.profile,
        body="Visible",
    )

    list_response = _client(user).get("/api/announcements/")
    detail_response = _client(user).get(f"/api/announcements/{announcement.id}/")
    list_row = _results(list_response.json())[0]
    detail = detail_response.json()

    assert list_row["reaction_counts"] == {"heart": 2}
    assert list_row["my_reactions"] == ["heart"]
    assert list_row["comments_count"] == 1
    assert detail["reaction_counts"] == {"heart": 2}
    assert detail["my_reactions"] == ["heart"]
    assert detail["comments_count"] == 1
