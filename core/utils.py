import copy
import os
import re
import secrets
import string
import urllib.request
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.models import DocumentTemplate, TemplateField

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
    response_content_type: str | None = None,
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
            params: dict[str, str] = {
                "Bucket": r2_bucket,
                "Key": file_key,
                "ResponseContentDisposition": disposition,
            }
            if response_content_type:
                params["ResponseContentType"] = response_content_type
            return client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expiry_seconds,
            )
        except Exception:
            pass

    return default_storage.url(file_key)


# ──────────────────────────────────────────────────────────────────────────────
# Document Template helpers
# ──────────────────────────────────────────────────────────────────────────────


def resolve_template_content(content: str, field_values: dict) -> str:
    """
    Replace all {{field_key}} placeholders in the HTML content string with
    the user-supplied values.

    Unknown placeholders are left intact so callers can detect un-filled fields.

    Args:
        content: The template's raw HTML content string.
        field_values: Mapping of field_key → replacement value.

    Returns:
        A new HTML string with all matching placeholders replaced.
    """
    result = content or ""
    for key, value in field_values.items():
        placeholder = "{{" + key + "}}"
        result = result.replace(placeholder, str(value) if value is not None else "")
    return result


def validate_template_fields(
    fields: list["TemplateField"], field_values: dict
) -> list[str]:
    """
    Validate that all required fields are present and non-empty in field_values.

    Args:
        fields: QuerySet or list of TemplateField instances.
        field_values: Mapping of field_key → supplied value from the request.

    Returns:
        List of human-readable labels for any missing required fields.
        An empty list means validation passed.
    """
    missing: list[str] = []
    for field in fields:
        if field.is_required:
            value = field_values.get(field.field_key)
            if value is None or value == "":
                missing.append(field.label)
    return missing


def clone_template(template: "DocumentTemplate", created_by) -> "DocumentTemplate":
    """
    Deep clone a DocumentTemplate as a new PRIVATE user-owned copy.

    The clone gets a 'Copy of …' prefix on its name, is always PRIVATE,
    is never a system template, and has all TemplateField rows duplicated.

    Args:
        template: The source DocumentTemplate to clone.
        created_by: The UserProfile that will own the new copy.

    Returns:
        The newly saved DocumentTemplate instance (with fields pre-fetched).
    """
    from core.enums import TemplateVisibility
    from core.models import DocumentTemplate, TemplateField

    new_template = DocumentTemplate(
        name=f"Copy of {template.name}",
        description=template.description,
        category=template.category,
        content=copy.deepcopy(template.content),
        visibility=TemplateVisibility.PRIVATE,
        status=template.status,
        is_system_template=False,
        is_active=True,
        created_by=created_by,
    )
    new_template.save()

    for field in template.fields.order_by("order", "id"):
        TemplateField.objects.create(
            template=new_template,
            label=field.label,
            field_key=field.field_key,
            field_type=field.field_type,
            placeholder=field.placeholder,
            default_value=field.default_value,
            is_required=field.is_required,
            options=copy.deepcopy(field.options) if field.options is not None else None,
            order=field.order,
        )

    return DocumentTemplate.objects.prefetch_related("fields").get(pk=new_template.pk)


def get_template_or_404(template_id) -> "DocumentTemplate":
    """
    Fetch an active DocumentTemplate by primary key or raise a structured 404.

    Raises:
        rest_framework.exceptions.NotFound with a structured error body when
        the template does not exist or has been soft-deleted (is_active=False).

    Args:
        template_id: The primary key of the template to fetch.

    Returns:
        DocumentTemplate instance with its fields pre-fetched.
    """
    from rest_framework.exceptions import NotFound

    from core.enums import ErrorCode
    from core.models import DocumentTemplate

    try:
        return (
            DocumentTemplate.objects.prefetch_related("fields")
            .select_related("created_by__user")
            .get(pk=template_id, is_active=True)
        )
    except (DocumentTemplate.DoesNotExist, ValueError, TypeError):
        exc = NotFound()
        exc.detail = {
            "code": ErrorCode.NOT_FOUND,
            "message": "Template not found.",
            "details": {},
        }
        raise exc


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
