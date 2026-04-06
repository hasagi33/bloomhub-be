from rest_framework import permissions

from .models import Permission


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


def has_asset_permission(user, feature_action):
    """Helper used by asset views to evaluate Asset Management permissions."""
    return _has_permission(user, "Asset Management", [feature_action])


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
            return self._is_hr_admin(request.user)

        return True

    def has_object_permission(self, request, view, obj):
        if self._is_hr_admin(request.user):
            return True

        if request.method in permissions.SAFE_METHODS:
            return obj.user == request.user

        return False

    def _is_hr_admin(self, user):
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
