from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import (
    Announcement,
    AnnouncementSettings,
    Permission,
    Role,
    UserProfile,
)


def _grant(profile: UserProfile, module_name: str, *actions: str) -> None:
    role = profile.role
    if role is None:
        role = Role.objects.create(name=f"intro-role-{profile.pk}")
        profile.role = role
        profile.save(update_fields=["role"])

    for action in actions:
        permission, _ = Permission.objects.get_or_create(
            module_name=module_name,
            feature_action=action,
        )
        role.permissions.add(permission)


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _hr_user(*announcement_actions: str) -> User:
    user = User.objects.create_user(username="hr", email="hr@test.com", password="x")
    role, _ = Role.objects.get_or_create(name="HR")
    user.profile.role = role
    user.profile.save(update_fields=["role"])
    _grant(user.profile, "Employee Profiles", "view_all_profiles")
    if announcement_actions:
        _grant(user.profile, "Announcements", *announcement_actions)
    return user


@pytest.mark.django_db
@override_settings(
    EMPLOYEE_INTRO_ANNOUNCEMENT_TEMPLATE=(
        "<p>Welcome {first_name} to {department} as {role}. Starts {start_date}.</p>"
    )
)
def test_employee_create_can_publish_intro_announcement_from_template():
    hr = _hr_user("create_announcements")

    response = _client(hr).post(
        "/api/employees/",
        {
            "email": "new.employee@test.com",
            "first_name": "New",
            "last_name": "Employee",
            "full_name": "New Employee",
            "department": "Engineering",
            "start_date": "2026-06-01",
            "publish_intro_announcement": True,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    profile = UserProfile.objects.get(email_address="new.employee@test.com")
    announcement = Announcement.objects.get()
    assert announcement.title == "Welcome New Employee"
    assert announcement.body == (
        "<p>Welcome New to Engineering as . Starts 2026-06-01.</p>"
    )
    assert announcement.type == Announcement.Type.CELEBRATION
    assert announcement.author == hr.profile
    assert profile.intro_announcement == announcement
    assert profile.intro_announcement_published_at is not None


@pytest.mark.django_db
def test_employee_create_auto_publishes_intro_announcement_without_flag():
    hr = _hr_user("create_announcements")

    response = _client(hr).post(
        "/api/employees/",
        {
            "email": "quiet.employee@test.com",
            "first_name": "Quiet",
            "last_name": "Employee",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    profile = UserProfile.objects.get(email_address="quiet.employee@test.com")
    announcement = Announcement.objects.get()
    assert announcement.title == "Welcome Quiet Employee"
    assert announcement.type == Announcement.Type.CELEBRATION
    assert announcement.author == hr.profile
    assert profile.intro_announcement == announcement


@pytest.mark.django_db
def test_employee_create_auto_intro_announcement_can_be_disabled():
    settings_obj = AnnouncementSettings.load()
    settings_obj.auto_employee_intro_on_employee_create = False
    settings_obj.save()
    hr = _hr_user("create_announcements")

    response = _client(hr).post(
        "/api/employees/",
        {
            "email": "quiet.employee@test.com",
            "first_name": "Quiet",
            "last_name": "Employee",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Announcement.objects.count() == 0


@pytest.mark.django_db
def test_registration_auto_publishes_intro_announcement():
    response = APIClient().post(
        "/api/auth/register/",
        {
            "username": "lowuser",
            "email": "lowuser@mail.com",
            "password": "testpass123",
            "password_confirm": "testpass123",
            "first_name": "Low",
            "last_name": "User",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    profile = UserProfile.objects.get(email_address="lowuser@mail.com")
    announcement = Announcement.objects.get()
    assert announcement.title == "Welcome Low User"
    assert announcement.type == Announcement.Type.CELEBRATION
    assert announcement.author == profile
    assert profile.intro_announcement == announcement


@pytest.mark.django_db
def test_registration_auto_intro_announcement_can_be_disabled():
    settings_obj = AnnouncementSettings.load()
    settings_obj.auto_employee_intro_on_registration = False
    settings_obj.save()

    response = APIClient().post(
        "/api/auth/register/",
        {
            "username": "lowuser",
            "email": "lowuser@mail.com",
            "password": "testpass123",
            "password_confirm": "testpass123",
            "first_name": "Low",
            "last_name": "User",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert Announcement.objects.count() == 0


@pytest.mark.django_db
def test_employee_update_can_publish_custom_intro_announcement():
    hr = _hr_user("create_announcements")
    employee = User.objects.create_user(
        username="employee", email="employee@test.com", password="x"
    )
    profile = employee.profile
    profile.full_name = "Existing Employee"
    profile.department = "Product"
    profile.save(update_fields=["full_name", "department"])

    response = _client(hr).patch(
        f"/api/employees/{profile.id}/",
        {
            "publish_intro_announcement": True,
            "intro_announcement_title": "Meet Existing",
            "intro_announcement_body": "<p>Say hi to Existing.</p>",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    announcement = Announcement.objects.get()
    assert announcement.title == "Meet Existing"
    assert announcement.body == "<p>Say hi to Existing.</p>"
    profile.refresh_from_db()
    assert profile.intro_announcement == announcement


@pytest.mark.django_db
def test_employee_intro_announcement_cannot_be_published_twice():
    hr = _hr_user("create_announcements")
    employee = User.objects.create_user(
        username="employee", email="employee@test.com", password="x"
    )
    profile = employee.profile
    profile.intro_announcement = Announcement.objects.create(
        title="Welcome Existing",
        body="<p>Existing.</p>",
        author=hr.profile,
    )
    profile.save(update_fields=["intro_announcement"])

    response = _client(hr).patch(
        f"/api/employees/{profile.id}/",
        {"publish_intro_announcement": True},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Announcement.objects.count() == 1


@pytest.mark.django_db
def test_employee_intro_announcement_requires_publisher_role():
    user = User.objects.create_user(username="profile-admin", password="x")
    role, _ = Role.objects.get_or_create(name="Employee")
    user.profile.role = role
    user.profile.save(update_fields=["role"])
    _grant(user.profile, "Employee Profiles", "view_all_profiles")

    response = _client(user).post(
        "/api/employees/",
        {
            "email": "blocked.employee@test.com",
            "first_name": "Blocked",
            "last_name": "Employee",
            "publish_intro_announcement": True,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Announcement.objects.count() == 0
    assert User.objects.filter(email="blocked.employee@test.com").exists() is False


@pytest.mark.django_db
def test_scheduled_intro_announcement_requires_schedule_permission():
    hr = _hr_user("create_announcements")
    scheduled_at = timezone.now() + timedelta(days=1)

    response = _client(hr).post(
        "/api/employees/",
        {
            "email": "scheduled.employee@test.com",
            "first_name": "Scheduled",
            "last_name": "Employee",
            "publish_intro_announcement": True,
            "intro_announcement_scheduled_at": scheduled_at.isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Announcement.objects.count() == 0


@pytest.mark.django_db
def test_scheduled_intro_announcement_sets_published_at_to_schedule():
    hr = _hr_user("create_announcements", "schedule_announcements")
    scheduled_at = timezone.now() + timedelta(days=1)

    response = _client(hr).post(
        "/api/employees/",
        {
            "email": "scheduled.employee@test.com",
            "first_name": "Scheduled",
            "last_name": "Employee",
            "publish_intro_announcement": True,
            "intro_announcement_scheduled_at": scheduled_at.isoformat(),
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    announcement = Announcement.objects.get()
    assert announcement.scheduled_at == scheduled_at
    assert announcement.published_at == scheduled_at
