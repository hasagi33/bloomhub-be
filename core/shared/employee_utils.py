"""Employee profile helpers (e.g. soft-delete / anonymization for DELETE on profile API)."""

import uuid

from django.contrib.auth.models import User

from ..models import UserProfile


def _delete_profile_files(instance: UserProfile) -> None:
    if instance.avatar:
        instance.avatar.delete(save=False)

    if not hasattr(instance, "documents"):
        return
    for doc in instance.documents.all():
        if doc.file:
            doc.file.delete(save=False)
        doc.delete()


def _delete_related_records(instance: UserProfile) -> None:
    if hasattr(instance, "project_assignments"):
        instance.project_assignments.all().delete()
    if hasattr(instance, "equipment_assignments"):
        instance.equipment_assignments.all().delete()
    if hasattr(instance, "salary_records"):
        instance.salary_records.all().delete()
    if hasattr(instance, "change_logs"):
        instance.change_logs.all().delete()


def _clear_many_to_many(instance: UserProfile) -> None:
    if hasattr(instance, "tech_tags"):
        instance.tech_tags.clear()


def _nullify_profile_pii(instance: UserProfile) -> None:
    instance.employee_id = None
    instance.full_name = "Deleted User"
    instance.email_address = None
    instance.department = None
    instance.phone_number = None
    instance.address = None
    instance.emergency_contact_name = None
    instance.emergency_contact_phone = None
    instance.birthday = None
    instance.career_level = None
    instance.cpf_level = None
    instance.role = None
    instance.manager = None
    instance.permissions = ""


def _mark_profile_inactive(instance: UserProfile) -> None:
    instance.employment_status = UserProfile.EmploymentStatus.INACTIVE
    instance.is_active = False
    instance.save()


def _anonymize_linked_user(user: User) -> None:
    user.username = f"deleted_{user.id}_{uuid.uuid4().hex[:8]}"
    user.email = ""
    user.first_name = "Deleted"
    user.last_name = "User"
    user.set_unusable_password()
    user.is_active = False
    user.save()


def soft_delete_employee_profile(instance: UserProfile) -> None:
    """Remove files and related rows, clear PII, soft-delete profile, anonymize Django User."""
    _delete_profile_files(instance)
    _delete_related_records(instance)
    _clear_many_to_many(instance)
    _nullify_profile_pii(instance)
    _mark_profile_inactive(instance)
    if instance.user:
        _anonymize_linked_user(instance.user)
