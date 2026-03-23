import os
import re
import secrets
import string
import urllib.request

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from google.auth.transport import requests
from google.oauth2 import id_token


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


def verify_google_id_token(token: str) -> dict:
    """
    Verify the Google ID token using google-auth and return its payload.
    Raises ValueError on invalid token.
    """
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    request = requests.Request()
    if client_id:
        return id_token.verify_oauth2_token(token, request, client_id)
    else:
        # Without client id, verify everything except the exact client ID match
        return id_token.verify_oauth2_token(token, request)


def upgrade_google_picture_url(url: str, size: int = 400) -> str:
    """
    Google profile picture URLs end with a size token such as =s96-c.
    Replace it with the requested size so we store a higher-res image.
    Falls back to appending =s<size>-c if no existing token is found.
    """
    upgraded = re.sub(r"=s\d+(-c)?$", f"=s{size}-c", url)
    if upgraded == url and not url.endswith("-c"):
        # No suffix found – append one
        upgraded = url.rstrip("/") + f"=s{size}-c"
    return upgraded


def download_and_save_avatar(profile, url: str) -> bool:
    """
    Download an image from `url` and save it to profile.avatar.
    For Google profile picture URLs the resolution is automatically
    upgraded to 400 px before downloading. The file is stored via
    Django's DEFAULT_FILE_STORAGE backend — Cloudflare R2 when
    R2 credentials are set, otherwise local filesystem as fallback.
    Returns True on success, False on failure.
    """
    try:
        url = upgrade_google_picture_url(url)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "BloomHub/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        profile.avatar.save(
            "avatar.png",
            ContentFile(raw, name=f"avatar-{profile.user_id}.png"),
            save=True,
        )
        return True
    except Exception:
        return False
