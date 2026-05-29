from datetime import date

import pytest
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Permission, Role, UserProfile
from core.services.celebrations import build_upcoming_profile_celebrations


def _grant(profile: UserProfile, *actions: str) -> None:
    role = profile.role
    if role is None:
        role = Role.objects.create(name=f"celebration-role-{profile.pk}")
        profile.role = role
        profile.save(update_fields=["role"])

    for action in actions:
        permission, _ = Permission.objects.get_or_create(
            module_name="Announcements",
            feature_action=action,
        )
        role.permissions.add(permission)


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _profile(
    username: str,
    *,
    full_name: str,
    birthday: date | None = None,
    start_date: date | None = None,
) -> UserProfile:
    user = User.objects.create_user(
        username=username,
        first_name=full_name.split()[0],
        last_name=full_name.split()[-1],
        password="x",
    )
    profile = user.profile
    profile.full_name = full_name
    profile.department = "Engineering"
    profile.birthday = birthday
    profile.start_date = start_date
    profile.save(update_fields=["full_name", "department", "birthday", "start_date"])
    return profile


@pytest.mark.django_db
def test_upcoming_celebrations_returns_profile_sourced_events(monkeypatch):
    monkeypatch.setattr(
        "core.services.celebrations.timezone.localdate",
        lambda: date(2026, 5, 28),
    )
    viewer = User.objects.create_user(username="viewer", password="x")
    _grant(viewer.profile, "view_birthdays", "view_anniversaries")
    _profile(
        "birthday",
        full_name="Birthday Person",
        birthday=date(1990, 6, 1),
    )
    _profile(
        "anniversary",
        full_name="Anniversary Person",
        start_date=date(2021, 5, 30),
    )

    response = _client(viewer).get("/api/celebrations/upcoming/?days=7")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "event_type": "anniversary",
            "event_date": "2026-05-30",
            "days_until": 2,
            "employee": {
                "id": User.objects.get(username="anniversary").profile.id,
                "full_name": "Anniversary Person",
                "department": "Engineering",
                "avatar_url": None,
            },
            "anniversary_years": 5,
        },
        {
            "event_type": "birthday",
            "event_date": "2026-06-01",
            "days_until": 4,
            "employee": {
                "id": User.objects.get(username="birthday").profile.id,
                "full_name": "Birthday Person",
                "department": "Engineering",
                "avatar_url": None,
            },
            "anniversary_years": None,
        },
    ]


@pytest.mark.django_db
def test_upcoming_celebrations_respects_event_type_permissions(monkeypatch):
    monkeypatch.setattr(
        "core.services.celebrations.timezone.localdate",
        lambda: date(2026, 5, 28),
    )
    viewer = User.objects.create_user(username="viewer", password="x")
    _grant(viewer.profile, "view_birthdays")
    _profile("birthday", full_name="Birthday Person", birthday=date(1990, 6, 1))
    _profile("anniversary", full_name="Anniversary Person", start_date=date(2021, 6, 1))

    list_response = _client(viewer).get("/api/celebrations/upcoming/?days=7")
    forbidden_response = _client(viewer).get(
        "/api/celebrations/upcoming/?days=7&type=anniversary"
    )

    assert list_response.status_code == status.HTTP_200_OK
    assert [row["event_type"] for row in list_response.json()] == ["birthday"]
    assert forbidden_response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_upcoming_celebrations_requires_view_permission():
    user = User.objects.create_user(username="no-access", password="x")

    response = _client(user).get("/api/celebrations/upcoming/")

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query",
    ["?days=0", "?days=366", "?days=abc", "?type=custom"],
)
def test_upcoming_celebrations_validates_query_params(query):
    user = User.objects.create_user(username=f"viewer-{query}", password="x")
    _grant(user.profile, "view_birthdays")

    response = _client(user).get(f"/api/celebrations/upcoming/{query}")

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_celebration_service_handles_year_boundary_and_excludes_first_year():
    _profile("birthday", full_name="Year Boundary", birthday=date(1990, 1, 2))
    _profile("future", full_name="Future Hire", start_date=date(2026, 1, 2))
    _profile("tenured", full_name="Tenured Hire", start_date=date(2020, 1, 3))

    events = build_upcoming_profile_celebrations(
        days=5,
        today=date(2025, 12, 30),
    )

    assert [
        (event["event_type"], event["employee"]["full_name"]) for event in events
    ] == [
        ("birthday", "Year Boundary"),
        ("anniversary", "Tenured Hire"),
    ]
    assert events[1]["anniversary_years"] == 6


@pytest.mark.django_db
def test_celebration_service_maps_feb_29_to_feb_28_in_non_leap_year():
    _profile("leap", full_name="Leap Person", birthday=date(1992, 2, 29))

    events = build_upcoming_profile_celebrations(
        days=1,
        event_types={"birthday"},
        today=date(2025, 2, 27),
    )

    assert len(events) == 1
    assert events[0]["event_date"] == date(2025, 2, 28)
    assert events[0]["days_until"] == 1


@pytest.mark.django_db
def test_celebration_service_excludes_inactive_profiles():
    inactive = _profile(
        "inactive", full_name="Inactive Person", birthday=date(1990, 6, 1)
    )
    inactive.is_active = False
    inactive.employment_status = UserProfile.EmploymentStatus.INACTIVE
    inactive.save(update_fields=["is_active", "employment_status"])

    events = build_upcoming_profile_celebrations(
        days=7,
        today=date(2026, 5, 28),
    )

    assert events == []
