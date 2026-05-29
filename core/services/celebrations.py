from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from django.db.models import Q
from django.utils import timezone

from core.enums import EmploymentStatus
from core.models import UserProfile

CelebrationType = Literal["birthday", "anniversary"]


def _is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _anniversary_date(source_date: date, year: int) -> date:
    if source_date.month == 2 and source_date.day == 29 and not _is_leap_year(year):
        return date(year, 2, 28)
    return date(year, source_date.month, source_date.day)


def _next_annual_occurrence(source_date: date, today: date) -> date:
    event_date = _anniversary_date(source_date, today.year)
    if event_date < today:
        event_date = _anniversary_date(source_date, today.year + 1)
    return event_date


def _profile_avatar_url(profile: UserProfile) -> str | None:
    if profile.avatar_url:
        return profile.avatar_url
    if not profile.avatar:
        return None
    try:
        return profile.avatar.url
    except Exception:
        return None


def _employee_payload(profile: UserProfile) -> dict:
    return {
        "id": profile.id,
        "full_name": profile.full_name
        or profile.user.get_full_name()
        or profile.user.username,
        "department": profile.department,
        "avatar_url": _profile_avatar_url(profile),
    }


def build_upcoming_profile_celebrations(
    *,
    days: int = 30,
    event_types: set[CelebrationType] | None = None,
    today: date | None = None,
) -> list[dict]:
    today = today or timezone.localdate()
    event_types = event_types or {"birthday", "anniversary"}
    window_end = today + timedelta(days=days)

    profiles = (
        UserProfile.objects.select_related("user")
        .filter(is_active=True, employment_status=EmploymentStatus.ACTIVE)
        .filter(Q(birthday__isnull=False) | Q(start_date__isnull=False))
        .order_by("full_name", "user__first_name", "user__last_name", "user__username")
    )

    events: list[dict] = []
    for profile in profiles:
        if "birthday" in event_types and profile.birthday:
            event_date = _next_annual_occurrence(profile.birthday, today)
            if today <= event_date <= window_end:
                events.append(
                    {
                        "event_type": "birthday",
                        "event_date": event_date,
                        "days_until": (event_date - today).days,
                        "employee": _employee_payload(profile),
                        "anniversary_years": None,
                    }
                )

        if "anniversary" in event_types and profile.start_date:
            event_date = _next_annual_occurrence(profile.start_date, today)
            years = event_date.year - profile.start_date.year
            if years >= 1 and today <= event_date <= window_end:
                events.append(
                    {
                        "event_type": "anniversary",
                        "event_date": event_date,
                        "days_until": (event_date - today).days,
                        "employee": _employee_payload(profile),
                        "anniversary_years": years,
                    }
                )

    return sorted(
        events,
        key=lambda event: (
            event["event_date"],
            event["employee"]["full_name"] or "",
            event["event_type"],
            event["employee"]["id"],
        ),
    )
