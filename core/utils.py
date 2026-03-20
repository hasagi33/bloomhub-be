import secrets
import string

from django.contrib.auth.models import User


def generate_secure_password(length=16):
    """Generate a secure random password."""
    return "".join(
        secrets.choice(string.ascii_letters + string.digits) for _ in range(length)
    )


def generate_unique_username(email):
    """Generate a unique username from an email base."""
    username = email.split("@")[0] if email else "user"
    base_username = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1
    return username


def get_role_permissions_bitmap(role):
    """Calculate the binary string permissions bitmap for a given role."""
    if not role:
        return ""
    bitmap = 0
    for perm in role.permissions.all():
        bitmap |= 1 << perm.bit_position
    return bin(bitmap)[2:]


def apply_profile_updates_and_save(profile, validated_data):
    """Assign role permissions if present, apply other fields, and save."""
    role = validated_data.get("role", None)
    if role:
        profile.permissions = get_role_permissions_bitmap(role)

    for attr, value in validated_data.items():
        setattr(profile, attr, value)

    profile.save()
    return profile
