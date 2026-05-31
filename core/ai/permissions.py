from __future__ import annotations

from rest_framework.exceptions import PermissionDenied

from core.permissions import IsHRAdminOrReadOnlyOwnProfile


def require_profile(user):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication is required.")
    profile = getattr(user, "profile", None)
    if profile is None:
        raise PermissionDenied("Authenticated user profile was not found.")
    return profile


def is_hr_admin(user) -> bool:
    return IsHRAdminOrReadOnlyOwnProfile()._is_hr_admin(user)


def is_privileged_global_viewer(user) -> bool:
    """Strict global-view gate: only superusers. is_staff alone is not enough."""
    return bool(getattr(user, "is_superuser", False))


def assert_authenticated(user) -> None:
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Authentication is required.")


def compact_user(user) -> dict:
    profile = getattr(user, "profile", None)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.get_full_name() or user.username,
        "profile_id": getattr(profile, "id", None),
        "role": getattr(getattr(profile, "role", None), "name", None),
        "is_staff": bool(getattr(user, "is_staff", False)),
        "is_superuser": bool(getattr(user, "is_superuser", False)),
    }
