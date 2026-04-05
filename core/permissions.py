from rest_framework import permissions

from .models import Permission


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


def has_asset_permission(user, feature_action: str) -> bool:
    """
    Check whether `user` holds a specific `Asset Management` feature/action permission.
    Superusers and staff are always granted access.
    """
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    try:
        profile = user.profile
    except Exception:
        return False
    try:
        perm = Permission.objects.get(
            module_name="Asset Management", feature_action=feature_action
        )
        return profile.has_permission(perm)
    except Permission.DoesNotExist:
        return False
