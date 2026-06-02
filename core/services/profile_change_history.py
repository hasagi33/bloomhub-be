from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User

from core.models import EmployeeProfileChangeHistory, UserProfile

# Pure helpers now live in core.utils — import them here for backward compat.
from core.utils import (  # noqa: F401  (re-exported for existing callers)
    normalize_enum_like,
    normalize_iso_date,
    normalize_manager_ids,
    normalize_trimmed_string,
)


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


def _as_manager_user_id(value: Any) -> int | None:
    """Coerce a manager reference to a positive user_id int."""
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


def cascade_cpf_level_definition_change(
    *,
    cpf_code: str,
    old_career_level: str | None,
    new_career_level: str | None,
    changed_by: User | None = None,
) -> None:
    """Propagate CPFLevel.career_level edits to all employees at that level.

    Updates each employee's stored career_level and emits a CAREER_LEVEL
    history row sourced to the CPF definition change.
    """
    if old_career_level == new_career_level:
        return

    employees = UserProfile.objects.filter(cpf_level=cpf_code)
    for employee in employees:
        previous = employee.career_level
        if previous == new_career_level:
            continue
        employee.career_level = new_career_level
        employee.save()
        log_employee_profile_change(
            employee=employee,
            field=EmployeeProfileChangeHistory.TrackedField.CAREER_LEVEL,
            old_value={"value": previous},
            new_value={"value": new_career_level},
            changed_by=changed_by,
            metadata={
                "source": "cpf_level_definition_change",
                "cpf_level": cpf_code,
            },
        )


def sync_employee_career_level_from_cpf(employee: UserProfile) -> bool:
    """Set employee.career_level to match CPFLevel.career_level. Returns True if changed."""
    from core.models import CPFLevel

    if not employee.cpf_level:
        return False
    cpf = CPFLevel.objects.filter(name=employee.cpf_level).first()
    if cpf is None:
        return False
    if employee.career_level == cpf.career_level:
        return False
    employee.career_level = cpf.career_level
    return True
