import os
import re
import secrets
import string
import urllib.request
from datetime import date, datetime
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
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


# ──────────────────────────────────────────────────────────────────────────────
# String / value normalisation helpers
# (used by profile_change_history and serializers)
# ──────────────────────────────────────────────────────────────────────────────


def normalize_trimmed_string(value: Any) -> str | None:
    """Strip whitespace; return None for blank/None input."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_enum_like(value: Any) -> str | None:
    """Normalise a string into lowercase enum-compatible form."""
    normalized = normalize_trimmed_string(value)
    return normalized.lower() if normalized else None


def normalize_iso_date(value: Any) -> str | None:
    """Convert a date / datetime / string to ISO-8601 date string (YYYY-MM-DD)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = normalize_trimmed_string(value)
    if not text:
        return None
    return text[:10]


def _as_manager_user_id(value: Any) -> int | None:
    """Coerce a manager reference (profile object, user_id int) to a positive user_id int."""
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


def normalize_manager_ids(values: Any) -> list[int]:
    """Return a sorted, deduplicated list of positive user_ids from a mixed input."""
    if values is None:
        return []
    normalized: set[int] = set()
    for raw in values:
        user_id = _as_manager_user_id(raw)
        if user_id is not None:
            normalized.add(user_id)
    return sorted(normalized)


# ──────────────────────────────────────────────────────────────────────────────
# Profile display helper
# ──────────────────────────────────────────────────────────────────────────────


def uploader_display_name(profile) -> str:
    """Return the best available display name for a UserProfile (or empty string)."""
    if not profile:
        return ""
    return (
        profile.full_name
        or profile.user.get_full_name().strip()
        or profile.user.username
    )


# ──────────────────────────────────────────────────────────────────────────────
# Storage helpers
# ──────────────────────────────────────────────────────────────────────────────


def generate_presigned_url(
    file_key: str,
    expiry_seconds: int = 600,
    *,
    inline: bool = False,
) -> str:
    """Return a short-lived URL for a stored file.

    Uses boto3 against Cloudflare R2 when R2 credentials are configured;
    falls back to Django's default_storage URL for local development.
    """
    r2_account_id = getattr(settings, "R2_ACCOUNT_ID", None)
    r2_access_key = getattr(settings, "R2_ACCESS_KEY_ID", None)
    r2_secret_key = getattr(settings, "R2_SECRET_ACCESS_KEY", None)
    r2_bucket = getattr(settings, "R2_BUCKET_NAME", None)

    if all([r2_account_id, r2_access_key, r2_secret_key, r2_bucket]):
        try:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=f"https://{r2_account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key,
                config=Config(signature_version="s3v4"),
                region_name="auto",
            )
            disposition = "inline" if inline else "attachment"
            return client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": r2_bucket,
                    "Key": file_key,
                    "ResponseContentDisposition": disposition,
                },
                ExpiresIn=expiry_seconds,
            )
        except Exception:
            pass

    return default_storage.url(file_key)


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
