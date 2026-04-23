from __future__ import annotations

from datetime import date, datetime
from typing import Any

from django.contrib.auth.models import User

from core.models import EmployeeProfileChangeHistory, UserProfile


def _role_payload(role) -> dict[str, Any] | None:
    if not role:
        return None
    return {"id": role.id, "name": role.name}


def log_employee_profile_change(
    *,
    employee: UserProfile,
    field: str,
    old_value: Any,
    new_value: Any,
    changed_by: User | None = None,
    metadata: dict[str, Any] | None = None,
) -> EmployeeProfileChangeHistory | None:
    """Create a tracked history row only when a value actually changes."""
    if old_value == new_value:
        return None

    return EmployeeProfileChangeHistory.objects.create(
        employee=employee,
        field=field,
        old_value=old_value,
        new_value=new_value,
        changed_by=changed_by,
        metadata=metadata or {},
    )


def role_value(role) -> dict[str, Any] | None:
    return _role_payload(role)


def normalize_trimmed_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_enum_like(value: Any) -> str | None:
    normalized = normalize_trimmed_string(value)
    return normalized.lower() if normalized else None


def normalize_iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize_trimmed_string(value)
    if not text:
        return None
    return text[:10]


def _as_manager_user_id(value: Any) -> int | None:
    if value is None:
        return None
    user_id = getattr(value, "user_id", None)
    if user_id is None:
        try:
            user_id = int(value)
        except (TypeError, ValueError):
            return None
    if user_id <= 0:
        return None
    return user_id


def normalize_manager_ids(values: Any) -> list[int]:
    if values is None:
        return []
    normalized: set[int] = set()
    for raw in values:
        user_id = _as_manager_user_id(raw)
        if user_id is not None:
            normalized.add(user_id)
    return sorted(normalized)


def manager_payload_from_ids(manager_user_ids: list[int]) -> dict[str, Any]:
    if not manager_user_ids:
        return {"ids": [], "names": []}

    managers = UserProfile.objects.select_related("user").filter(
        user_id__in=manager_user_ids
    )
    name_by_user_id = {}
    for manager in managers:
        display_name = (
            manager.full_name
            or manager.user.get_full_name().strip()
            or manager.user.username
        )
        name_by_user_id[manager.user_id] = display_name

    names = [name_by_user_id.get(user_id, str(user_id)) for user_id in manager_user_ids]
    return {"ids": manager_user_ids, "names": names}
