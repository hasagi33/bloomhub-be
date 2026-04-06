import pytest
from django.contrib.auth.models import User

from core.models import Permission, UserProfile


@pytest.mark.django_db
class TestSuperuserPermissions:
    """Test that superusers automatically get all permissions assigned."""

    def test_superuser_gets_all_permissions(self):
        """When a superuser is created, their profile should have all permissions."""
        # Create some permissions first
        for i in range(5):
            Permission.objects.create(
                module_name=f"module_{i}",
                feature_action=f"action_{i}",
            )

        total_permissions = Permission.objects.count()
        assert total_permissions == 5

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

    def test_regular_user_no_auto_permissions(self):
        """When a regular user is created, they should have no permissions."""
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

        # Verify no permissions are set
        assert profile.permissions == ""

        # Verify user has no permissions
        for permission in Permission.objects.all():
            assert not profile.has_permission(permission)

    def test_superuser_with_no_permissions_in_db(self):
        """Superuser created when no permissions exist should have empty bitmap."""
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

        # Verify the permissions bitmap is empty (no permissions to assign)
        assert profile.permissions == ""
