import pytest
from django.contrib.auth.models import User

from core.models import (
    ASSET_MANAGEMENT_MODULE,
    DEFAULT_ASSET_PERMISSION_ACTIONS,
    DEFAULT_USER_ASSET_PERMISSION_ACTIONS,
    Permission,
    UserProfile,
)


@pytest.mark.django_db
class TestSuperuserPermissions:
    """Test that superusers automatically get all permissions assigned."""

    def test_superuser_gets_all_permissions(self):
        """When a superuser is created, their profile should have all permissions."""
        initial_permission_count = Permission.objects.count()

        # Create some permissions first
        for i in range(5):
            Permission.objects.create(
                module_name=f"module_{i}",
                feature_action=f"action_{i}",
            )

        total_permissions = Permission.objects.count()
        assert total_permissions == initial_permission_count + 5

        # Create a superuser
        superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="securepassword",
        )

        # Verify the profile was created
        profile = UserProfile.objects.get(user=superuser)
        assert profile is not None

        # Verify the permissions bitmap is set correctly
        # Permissions are created with bit_position 1, 2, 3, 4, 5
        # So the bitmap should have bits 1-5 set: 111110 (binary) = 62 (decimal)
        actual_bitmap = int(profile.permissions, 2) if profile.permissions else 0
        assert actual_bitmap > 0  # Should have some permissions set

        # Verify superuser has all permissions
        for permission in Permission.objects.all():
            assert profile.has_permission(permission)

    def test_regular_user_gets_default_asset_permissions_only(self):
        """When a regular user is created, they should get default asset permissions only."""
        # Create some permissions
        for i in range(3):
            Permission.objects.create(
                module_name=f"module_{i}",
                feature_action=f"action_{i}",
            )

        # Create a regular user
        regular_user = User.objects.create_user(
            username="john",
            email="john@example.com",
            password="password123",
        )

        # Verify the profile was created
        profile = UserProfile.objects.get(user=regular_user)
        assert profile is not None

        default_asset_permissions = Permission.objects.filter(
            module_name=ASSET_MANAGEMENT_MODULE,
            feature_action__in=DEFAULT_USER_ASSET_PERMISSION_ACTIONS,
        )
        assert default_asset_permissions.count() == len(
            DEFAULT_USER_ASSET_PERMISSION_ACTIONS
        )

        # Verify user has default asset permissions and no custom permissions
        for permission in Permission.objects.all():
            expected = (
                permission.module_name == ASSET_MANAGEMENT_MODULE
                and permission.feature_action in DEFAULT_USER_ASSET_PERMISSION_ACTIONS
            )
            assert profile.has_permission(permission) is expected

    def test_superuser_with_no_preexisting_permissions_gets_all_created_defaults(self):
        """Superuser created with no preexisting permissions should get all created defaults."""
        # Ensure no permissions exist
        Permission.objects.all().delete()

        # Create a superuser
        superuser = User.objects.create_superuser(
            username="admin2",
            email="admin2@example.com",
            password="securepassword",
        )

        # Verify the profile was created
        profile = UserProfile.objects.get(user=superuser)
        assert profile is not None

        # The profile signal creates defaults before the superuser all-permission grant.
        created_permissions = Permission.objects.all()
        assert created_permissions.count() == len(DEFAULT_ASSET_PERMISSION_ACTIONS)
        for permission in created_permissions:
            assert permission.module_name == ASSET_MANAGEMENT_MODULE
            assert permission.feature_action in DEFAULT_ASSET_PERMISSION_ACTIONS
            assert profile.has_permission(permission)
