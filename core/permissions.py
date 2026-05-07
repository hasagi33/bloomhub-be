from typing import Any, cast

from rest_framework import permissions

from .enums import ReviewNoteVisibility
from .models import Permission

ASSET_PERMISSION_KEYS = [
    "view_own_assets",
    "view_team_assets",
    "view_all_assets",
    "assign_assets",
    "update_asset_condition",
    "track_warranty",
    "initiate_asset_return",
    "process_asset_return",
    "log_asset_lost",
    "generate_qr_codes",
    "view_asset_history",
    "configure_asset_types",
    "export_inventory",
]

ASSET_PERMISSION_ALIASES = {
    "view_own_assets": ["view_own_assets", "view_own_assigned_assets"],
    "assign_assets": ["assign_assets", "assign_assets_to_employees"],
    "process_asset_return": ["process_asset_return", "approve_asset_return"],
}


def _asset_permission_names(feature_action: str) -> list[str]:
    return ASSET_PERMISSION_ALIASES.get(feature_action, [feature_action])


def _get_user_profile(user):
    """Return the related profile when available, otherwise None."""
    try:
        return user.profile
    except Exception:
        return None


def _has_permission(user, module_name, feature_actions):
    """Check if a user has at least one permission in a module/action set."""
    if not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    for action in feature_actions:
        try:
            perm = Permission.objects.get(
                module_name=module_name,
                feature_action=action,
            )
        except Permission.DoesNotExist:
            continue

        if profile.has_permission(perm):
            return True

    return False


def has_asset_permission(user, feature_action: str) -> bool:
    """
    Check whether `user` holds a specific `Asset Management` feature/action permission.
    Superusers and staff are always granted access.
    """
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    asset_permissions = Permission.objects.filter(
        module_name="Asset Management",
        feature_action__in=_asset_permission_names(feature_action),
    )
    return any(profile.has_permission(perm) for perm in asset_permissions)


def get_asset_permissions(user) -> list[str]:
    """Return canonical asset permission keys held by the user."""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return list(ASSET_PERMISSION_KEYS)
    return [
        permission
        for permission in ASSET_PERMISSION_KEYS
        if has_asset_permission(user, permission)
    ]


def get_asset_scope(user) -> str:
    if has_asset_permission(user, "view_all_assets"):
        return "all"
    if has_asset_permission(user, "view_team_assets"):
        return "team"
    if has_asset_permission(user, "view_own_assets"):
        return "own"
    return "none"


def get_asset_capabilities(user) -> dict[str, bool]:
    return {
        "can_view_any_assets": get_asset_scope(user) != "none",
        "can_create_assets": has_asset_permission(user, "configure_asset_types"),
        "can_update_assets": has_asset_permission(user, "configure_asset_types"),
        "can_delete_assets": has_asset_permission(user, "configure_asset_types"),
        "can_assign_assets": has_asset_permission(user, "assign_assets"),
        "can_request_return": has_asset_permission(user, "initiate_asset_return"),
        "can_process_return": has_asset_permission(user, "process_asset_return"),
        "can_export_inventory": has_asset_permission(user, "export_inventory"),
        "can_view_asset_history": has_asset_permission(user, "view_asset_history"),
        "can_update_asset_condition": has_asset_permission(
            user, "update_asset_condition"
        )
        or has_asset_permission(user, "log_asset_lost"),
        "can_generate_qr_codes": has_asset_permission(user, "generate_qr_codes"),
    }


def can_view_asset(user, asset) -> bool:
    scope = get_asset_scope(user)
    if scope == "all":
        return True
    if scope == "none":
        return False

    profile = _get_user_profile(user)
    if profile is None:
        return False

    assignment = asset.current_assignment
    if not assignment:
        return False
    if assignment.employee_id == profile.id:
        return True
    if scope == "team":
        return profile.direct_reports.filter(id=assignment.employee_id).exists()
    return False


def can_view_assignment(user, assignment) -> bool:
    scope = get_asset_scope(user)
    if scope == "all":
        return True
    if scope == "none":
        return False

    profile = _get_user_profile(user)
    if profile is None:
        return False

    if assignment.employee_id == profile.id:
        return True
    if scope == "team":
        return profile.direct_reports.filter(id=assignment.employee_id).exists()
    return False


def get_asset_object_capabilities(user, asset) -> dict[str, bool]:
    can_configure = has_asset_permission(user, "configure_asset_types")
    can_assign = has_asset_permission(user, "assign_assets")
    can_process_return = has_asset_permission(user, "process_asset_return")
    can_update_condition = has_asset_permission(
        user, "update_asset_condition"
    ) or has_asset_permission(user, "log_asset_lost")

    can_request_return = False
    profile = _get_user_profile(user)
    assignment = asset.current_assignment
    if profile and assignment and assignment.employee_id == profile.id:
        can_request_return = has_asset_permission(user, "initiate_asset_return")

    return {
        "can_view": can_view_asset(user, asset),
        "can_update": can_configure,
        "can_delete": can_configure,
        "can_assign": can_assign and asset.is_available,
        "can_request_return": can_request_return,
        "can_process_return": can_process_return,
        "can_view_history": has_asset_permission(user, "view_asset_history"),
        "can_update_condition": can_update_condition,
    }


def can_view_return_checklist(user, assignment) -> bool:
    """Check whether a user can view return checklist/details for an assignment."""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    if assignment.employee_id == profile.id:
        return True

    return has_asset_permission(user, "process_asset_return")


class IsEmployeeOrHR(permissions.BasePermission):
    """Allow authenticated users with a profile, plus staff/superusers."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True

        return _get_user_profile(user) is not None


class IsHRAdminForAdjustment(permissions.BasePermission):
    """Allow users that can administratively adjust leave balances."""

    def has_permission(self, request, view):
        return _has_permission(
            request.user,
            "Vacations",
            ["adjust_balances", "override_requests", "configure_leave_types"],
        )


class IsManagerForApproval(permissions.BasePermission):
    """Allow managers/HR to approve team leave requests."""

    def has_permission(self, request, view):
        return _has_permission(
            request.user,
            "Vacations",
            ["approve_team_requests", "override_requests", "adjust_balances"],
        )

    def has_object_permission(self, request, view, obj):
        user = request.user
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True

        manager_profile = _get_user_profile(user)
        if manager_profile is None:
            return False

        try:
            employee_profile = obj.employee
        except Exception:
            return False

        # UserProfile.managers is a self-referential M2M that stores reporting lines.
        return employee_profile.managers.filter(pk=manager_profile.pk).exists()


class IsHRAdminOrReadOnlyOwnProfile(permissions.BasePermission):
    """
    Custom permission for Employee Profiles:
    - HR admins (those with 'add_remove_employees' or 'view_all_profiles' permission on the 'Employee Profiles' module) have full access.
    - Regular employees can view their own profile (GET), but cannot modify or delete.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        if request.method == "POST":
            return self._is_hr_admin(cast(Any, request.user))

        return True

    def has_object_permission(self, request, view, obj):
        if self._is_hr_admin(cast(Any, request.user)):
            return True

        if request.method in permissions.SAFE_METHODS:
            return obj.user == request.user

        return False

    def _is_hr_admin(self, user: Any):
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True

        try:
            profile = user.profile
        except Exception:
            return False

        has_view = False
        has_add = False

        try:
            perm_view_all = Permission.objects.get(
                module_name="Employee Profiles", feature_action="view_all_profiles"
            )
            has_view = profile.has_permission(perm_view_all)
        except Permission.DoesNotExist:
            pass

        try:
            perm_add_remove = Permission.objects.get(
                module_name="Employee Profiles", feature_action="add_remove_employees"
            )
            has_add = profile.has_permission(perm_add_remove)
        except Permission.DoesNotExist:
            pass

        return has_view or has_add


def has_review_permission(user, feature_action):
    """Helper used by review views to evaluate Reviews permissions."""
    return _has_permission(user, "Reviews", [feature_action])


def _is_review_hr_admin(user):
    """Review admins are staff/superusers or users with broad review admin rights."""
    return _has_permission(
        user,
        "Reviews",
        [
            "view_any_review_history",
            "configure_review_templates",
            "set_review_cycles",
            "export_review_data",
            "edit_delete_reviews",
        ],
    )


def _is_employee_manager_of(manager_profile, employee_profile):
    """Return True when manager_profile is configured as manager of employee_profile."""
    try:
        return employee_profile.managers.filter(pk=manager_profile.pk).exists()
    except Exception:
        return False


def can_view_review(user, review):
    """Object-level review visibility policy."""
    if not getattr(user, "is_authenticated", False):
        return False

    if _is_review_hr_admin(user):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    # Employee can view own reviews when granted.
    if getattr(review, "employee_id", None) == profile.id and has_review_permission(
        user, "view_own_reviews"
    ):
        return True

    # Assigned reviewer can view the review.
    if getattr(review, "reviewer_id", None) == profile.id and _has_permission(
        user,
        "Reviews",
        [
            "view_team_reviews",
            "create_review_direct_report",
            "schedule_reviews",
            "edit_delete_reviews",
        ],
    ):
        return True

    # Managers can view direct reports when granted.
    if has_review_permission(user, "view_team_reviews") and _is_employee_manager_of(
        profile,
        review.employee,
    ):
        return True

    return False


def can_edit_review(user, review):
    """Object-level review edit policy."""
    if not getattr(user, "is_authenticated", False):
        return False

    if _is_review_hr_admin(user):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    if getattr(review, "reviewer_id", None) == profile.id and _has_permission(
        user,
        "Reviews",
        [
            "create_review_direct_report",
            "schedule_reviews",
            "edit_delete_reviews",
        ],
    ):
        return True

    if has_review_permission(user, "create_review_direct_report") and (
        _is_employee_manager_of(profile, review.employee)
    ):
        return True

    return False


def can_edit_review_note(user, note):
    """
    Notes editability policy:
    - private: reviewer + HR/admin
    - shared: reviewer + employee + HR/admin
    """
    review = note.review
    if _is_review_hr_admin(user):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    is_reviewer = getattr(review, "reviewer_id", None) == profile.id
    is_employee = getattr(review, "employee_id", None) == profile.id

    if note.visibility == ReviewNoteVisibility.PRIVATE:
        return is_reviewer and has_review_permission(user, "add_private_feedback")

    return (is_reviewer and has_review_permission(user, "add_shared_feedback")) or (
        is_employee and has_review_permission(user, "initiate_self_review")
    )


def can_attach_review_documents(user, review):
    """Attachment permission policy for a review."""
    if _is_review_hr_admin(user):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    if not has_review_permission(user, "attach_documents"):
        return False

    if getattr(review, "reviewer_id", None) == profile.id:
        return True
    if getattr(review, "employee_id", None) == profile.id:
        return True
    if _is_employee_manager_of(profile, review.employee):
        return True
    return False


class IsReviewViewer(permissions.BasePermission):
    """Allow users who can view review records."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if _is_review_hr_admin(user):
            return True
        return _get_user_profile(user) is not None

    def has_object_permission(self, request, view, obj):
        return can_view_review(request.user, obj)


class IsReviewCreator(permissions.BasePermission):
    """Allow users who can schedule/create reviews."""

    def has_permission(self, request, view):
        return _has_permission(
            request.user,
            "Reviews",
            [
                "schedule_reviews",
                "create_review_direct_report",
                "set_review_cycles",
            ],
        )


class IsReviewEditor(permissions.BasePermission):
    """Allow users who can edit review records."""

    def has_permission(self, request, view):
        return _get_user_profile(request.user) is not None

    def has_object_permission(self, request, view, obj):
        return can_edit_review(request.user, obj)
