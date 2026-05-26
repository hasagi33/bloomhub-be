from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from core.enums import ReviewNoteVisibility
from core.models import Asset, Assignment, Permission
from core.permissions import (
    ASSET_PERMISSION_ALIASES,
    ASSET_PERMISSION_KEYS,
    IsCPFLevelChangeEditor,
    IsEmployeeOrHR,
    IsHRAdminForAdjustment,
    IsHRAdminOrReadOnlyOwnProfile,
    IsManagerForApproval,
    IsReviewCreator,
    IsReviewEditor,
    IsReviewViewer,
    IsTrainingBudgetEditor,
    _asset_permission_names,
    _get_user_profile,
    _has_permission,
    _is_employee_manager_of,
    _is_review_hr_admin,
    can_attach_review_documents,
    can_edit_review,
    can_edit_review_note,
    can_manage_cpf_level_changes,
    can_view_asset,
    can_view_asset_maintenance_logs,
    can_view_assignment,
    can_view_return_checklist,
    can_view_review,
    can_view_training_budget,
    get_asset_capabilities,
    get_asset_object_capabilities,
    get_asset_permissions,
    get_asset_scope,
    has_asset_permission,
    has_review_permission,
)


def _create_user(username: str, email: str | None = None):
    user = User.objects.create_user(
        username=username, email=email or f"{username}@example.com", password="x"
    )
    return user, user.profile


@pytest.mark.django_db
def test_asset_permission_helpers_and_object_capabilities():
    perms = {
        key: Permission.objects.get_or_create(
            module_name="Asset Management", feature_action=key
        )[0]
        for key in ASSET_PERMISSION_KEYS
    }
    manager_user, manager_profile = _create_user("manager")
    employee_user, employee_profile = _create_user("employee")
    employee_profile.managers.add(manager_profile)
    manager_profile.add_permission(perms["view_all_assets"])
    manager_profile.add_permission(perms["assign_assets"])
    manager_profile.add_permission(perms["view_asset_history"])
    manager_profile.add_permission(perms["process_asset_return"])
    manager_profile.add_permission(perms["log_asset_replacement"])
    manager_profile.add_permission(perms["generate_qr_codes"])
    manager_profile.add_permission(perms["configure_asset_types"])
    manager_profile.add_permission(perms["view_team_assets"])

    asset = Asset.objects.create(
        asset_id="LAP-1",
        name="Laptop",
        category="laptops",
        purchase_date="2026-01-01",
        status="active",
    )
    assignment = Assignment.objects.create(
        asset=asset, employee=employee_profile, assigned_by=manager_profile
    )
    free_asset = Asset.objects.create(
        asset_id="LAP-2",
        name="Laptop 2",
        category="laptops",
        purchase_date="2026-01-01",
        status="active",
    )

    assert (
        _asset_permission_names("view_own_assets")
        == ASSET_PERMISSION_ALIASES["view_own_assets"]
    )
    assert _asset_permission_names("custom") == ["custom"]
    assert _get_user_profile(manager_user) == manager_profile
    assert _get_user_profile(SimpleNamespace(profile=None)) is None

    assert (
        _has_permission(manager_user, "Asset Management", ["view_all_assets"]) is True
    )
    assert has_asset_permission(manager_user, "view_all_assets") is True
    held = get_asset_permissions(manager_user)
    assert "view_all_assets" in held
    assert "assign_assets" in held
    assert get_asset_scope(manager_user) == "all"
    assert can_view_asset_maintenance_logs(manager_user) is True
    assert get_asset_capabilities(manager_user)["can_assign_assets"] is True
    assert can_view_asset(manager_user, asset) is True
    assert can_view_assignment(manager_user, assignment) is True
    assert get_asset_object_capabilities(manager_user, free_asset)["can_assign"] is True
    assert can_view_return_checklist(manager_user, assignment) is True

    assert get_asset_scope(employee_user) == "own"


@pytest.mark.django_db
def test_review_and_hr_permission_helpers(monkeypatch):
    manager_user, manager_profile = _create_user("review-manager")
    employee_user, employee_profile = _create_user("review-employee")
    employee_profile.managers.add(manager_profile)

    monkeypatch.setattr(
        "core.permissions._has_permission",
        lambda user, module, actions: (
            module == "Reviews" and actions == ["view_team_reviews"]
        )
        or (
            module == "Employee Profiles"
            and actions == ["view_all_profiles", "add_remove_employees"]
        ),
    )

    review = SimpleNamespace(
        employee=employee_profile,
        employee_id=employee_profile.id,
        reviewer_id=manager_profile.id,
    )
    note = SimpleNamespace(review=review, visibility=ReviewNoteVisibility.PRIVATE)

    assert has_review_permission(manager_user, "view_team_reviews") is True
    assert _is_review_hr_admin(manager_user) is False
    assert _is_employee_manager_of(manager_profile, employee_profile) is True
    assert can_view_review(manager_user, review) is True
    assert can_edit_review(manager_user, review) is False
    assert can_edit_review_note(manager_user, note) is False
    assert can_attach_review_documents(manager_user, review) is False

    request = SimpleNamespace(user=manager_user, method="GET")
    assert IsReviewViewer().has_permission(request, None) is True
    assert IsReviewCreator().has_permission(request, None) is False
    assert IsReviewEditor().has_permission(request, None) is True
    assert IsHRAdminForAdjustment().has_permission(request, None) is False
    assert IsManagerForApproval().has_permission(request, None) is False
    assert IsEmployeeOrHR().has_permission(request, None) is True

    admin_user, _ = _create_user("admin-review")
    admin_user.is_staff = True
    admin_user.save(update_fields=["is_staff"])
    admin_request = SimpleNamespace(user=admin_user, method="POST")
    assert IsHRAdminOrReadOnlyOwnProfile().has_permission(admin_request, None) is True
    assert IsCPFLevelChangeEditor().has_permission(admin_request, None) is True


@pytest.mark.django_db
def test_budget_and_cpf_permissions(monkeypatch):
    user, profile = _create_user("budget-user")
    manager_user, manager_profile = _create_user("budget-manager")
    profile.managers.add(manager_profile)

    monkeypatch.setattr(
        "core.permissions._has_permission",
        lambda user, module, actions: (
            module == "Training" and actions == ["configure_budget"]
        )
        or (
            module == "Employee Profiles"
            and actions == ["view_all_profiles", "add_remove_employees"]
        ),
    )

    budget = SimpleNamespace(employee_id=profile.id, employee=profile)
    assert can_view_training_budget(user, budget) is True
    assert can_manage_cpf_level_changes(user) is True

    request = SimpleNamespace(user=user, method="POST")
    assert IsTrainingBudgetEditor().has_permission(request, None) is True
    assert IsTrainingBudgetEditor().has_object_permission(request, None, budget) is True
    assert IsCPFLevelChangeEditor().has_permission(request, None) is True

    budget_other = SimpleNamespace(
        employee_id=manager_profile.id, employee=manager_profile
    )
    assert can_view_training_budget(user, budget_other) is False

    safe_request = SimpleNamespace(user=user, method="GET")
    assert IsHRAdminOrReadOnlyOwnProfile().has_permission(safe_request, None) is True
    own_obj = SimpleNamespace(user=user)
    other_obj = SimpleNamespace(user=manager_user)
    assert (
        IsHRAdminOrReadOnlyOwnProfile().has_object_permission(
            safe_request, None, own_obj
        )
        is True
    )
    assert (
        IsHRAdminOrReadOnlyOwnProfile().has_object_permission(
            safe_request, None, other_obj
        )
        is False
    )
