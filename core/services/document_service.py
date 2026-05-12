from __future__ import annotations

import csv
import io
import mimetypes
import zipfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from core.constants import (
    DOCUMENT_CATEGORY_DEFAULT_VISIBILITY,
    DOCUMENT_ROLE_RANK_ADMIN,
    DOCUMENT_ROLE_RANK_EMPLOYEE,
    DOCUMENT_ROLE_RANK_HR,
    DOCUMENT_ROLE_RANK_MANAGER,
)
from core.enums import DocumentAccessRole
from core.models import Document, DocumentCategoryDefault, DocumentVersion, Permission
from core.permissions import _get_user_profile
from core.utils import generate_presigned_url, uploader_display_name

# Backward-compat alias so callers that import DocumentRole from this module
# continue to work while the codebase migrates to DocumentAccessRole.
DocumentRole = DocumentAccessRole


# ──────────────────────────────────────────
# Role resolution
# ──────────────────────────────────────────


DOCUMENT_ROLE_RANK: dict[str, int] = {
    DocumentAccessRole.ADMIN.value: DOCUMENT_ROLE_RANK_ADMIN,
    DocumentAccessRole.HR.value: DOCUMENT_ROLE_RANK_HR,
    DocumentAccessRole.MANAGER.value: DOCUMENT_ROLE_RANK_MANAGER,
    DocumentAccessRole.EMPLOYEE.value: DOCUMENT_ROLE_RANK_EMPLOYEE,
}


def _profile_role_name(profile) -> str:
    role = getattr(profile, "role", None)
    return (getattr(role, "name", "") or "").lower()


def is_user_manager(user) -> bool:
    profile = _get_user_profile(user)
    if profile is None:
        return False
    return profile.direct_reports.exists()


def is_admin_user(user) -> bool:
    return resolve_document_role(user) == DocumentRole.ADMIN


def resolve_document_role(user) -> DocumentAccessRole:
    """Determine the effective document access role for a user."""
    if not getattr(user, "is_authenticated", False):
        return DocumentRole.EMPLOYEE

    if getattr(user, "is_superuser", False):
        return DocumentRole.ADMIN

    if getattr(user, "is_staff", False):
        return DocumentRole.HR

    role_name = _profile_role_name(_get_user_profile(user))

    if role_name == DocumentRole.ADMIN.value:
        return DocumentRole.ADMIN
    if role_name.startswith(DocumentRole.HR.value):
        return DocumentRole.HR

    if is_user_manager(user):
        return DocumentRole.MANAGER

    return DocumentRole.EMPLOYEE


def is_hr_or_admin(user) -> bool:
    """Return True when user has HR-level or admin-level document access."""
    return resolve_document_role(user) in (DocumentRole.HR, DocumentRole.ADMIN)


def has_document_permission(user, feature_action: str) -> bool:
    """Check the bitmap/role permission model for a Documents feature action."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True

    profile = _get_user_profile(user)
    if profile is None:
        return False

    try:
        permission = Permission.objects.get(
            module_name="Documents",
            feature_action=feature_action,
        )
    except Permission.DoesNotExist:
        return False

    return profile.has_permission(permission)


# ──────────────────────────────────────────
# Access control
# ──────────────────────────────────────────


def _effective_allowed_roles(document: Document) -> list[str]:
    roles = document.allowed_roles or []
    if not roles:
        return [DocumentAccessRole.EMPLOYEE.value]
    return list(roles)


def _min_allowed_rank(allowed: list[str]) -> int:
    ranks = [DOCUMENT_ROLE_RANK[r] for r in allowed if r in DOCUMENT_ROLE_RANK]
    if not ranks:
        return DOCUMENT_ROLE_RANK_EMPLOYEE
    return min(ranks)


def _user_emails(user) -> set[str]:
    """All lowercased emails associated with the user (auth + profile)."""
    emails: set[str] = set()
    primary = (getattr(user, "email", "") or "").strip().lower()
    if primary:
        emails.add(primary)
    profile = getattr(user, "profile", None)
    if profile is not None:
        profile_email = (getattr(profile, "email_address", "") or "").strip().lower()
        if profile_email:
            emails.add(profile_email)
    return emails


def is_user_signer_of(user, document: Document) -> bool:
    """True if the user is on the signer list for this document."""
    emails = _user_emails(user)
    if not emails:
        return False
    return document.signers.filter(email__in=list(emails)).exists()


def _is_document_owner(user, document: Document) -> bool:
    """True when the user is the uploader (or owning employee) of the document."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    if document.uploaded_by_id and document.uploaded_by_id == profile.pk:
        return True
    if getattr(document, "employee_id", None) and document.employee_id == profile.pk:
        return True
    return False


def is_document_accessible(user, document: Document) -> bool:
    """Check whether a user can access a specific document."""
    role = resolve_document_role(user)
    if role == DocumentRole.ADMIN:
        return True

    # Signers always retain access to documents they need to sign,
    # regardless of role or visibility-scope restrictions.
    if is_user_signer_of(user, document):
        return True

    # Owner of the document always has access.
    if _is_document_owner(user, document):
        return True

    scope = getattr(document, "visibility_scope", Document.VisibilityScope.ROLES)

    # Private — only owner, signers, admins (already short-circuited above).
    if scope == Document.VisibilityScope.ONLY_ME:
        return False

    # Project-group scope: no project-membership model exists yet, so
    # we currently restrict to owner + signers + admins. Falls through
    # to role-rank check otherwise to stay backwards-compatible until
    # project membership lookup is implemented.
    if scope == Document.VisibilityScope.PROJECT_GROUP:
        return False

    user_rank = DOCUMENT_ROLE_RANK.get(role.value, 0)
    allowed = _effective_allowed_roles(document)
    return user_rank >= _min_allowed_rank(allowed)


def filter_accessible_documents(user, queryset):
    """Return only the subset of the queryset the user is allowed to see."""
    if is_admin_user(user):
        return queryset
    return [doc for doc in queryset if is_document_accessible(user, doc)]


# ──────────────────────────────────────────
# Visibility writes
# ──────────────────────────────────────────


def update_document_visibility(
    document: Document,
    allowed_roles: list[str],
    visibility_scope: str | None = None,
) -> Document:
    document.allowed_roles = list(allowed_roles)
    update_fields = ["allowed_roles", "updated_at"]
    if visibility_scope is not None:
        document.visibility_scope = visibility_scope
        update_fields.append("visibility_scope")
    document.save(update_fields=update_fields)
    return document


def get_document_category_defaults() -> dict[str, list[str]]:
    defaults = {k: list(v) for k, v in DOCUMENT_CATEGORY_DEFAULT_VISIBILITY.items()}
    for row in DocumentCategoryDefault.objects.all():
        defaults[row.category] = list(row.allowed_roles or [])
    return defaults


def set_document_category_default(
    category: str, allowed_roles: list[str]
) -> DocumentCategoryDefault:
    row, _ = DocumentCategoryDefault.objects.update_or_create(
        category=category,
        defaults={"allowed_roles": list(allowed_roles)},
    )
    return row


# ──────────────────────────────────────────
# Storage helpers
# ──────────────────────────────────────────
# generate_presigned_url is imported from core.utils (single source of truth).


def _delete_file_key(file_key: str) -> None:
    """Best-effort remove a single file from storage."""
    if not file_key:
        return
    try:
        default_storage.delete(file_key)
    except Exception:
        pass


# ──────────────────────────────────────────
# Single-document operations
# ──────────────────────────────────────────


def hard_delete_document(document: Document) -> None:
    """Delete the document record and all associated files from storage."""
    _delete_file_key(document.file_key)
    for version in document.versions.all():
        _delete_file_key(version.file_key)
    document.delete()


def archive_document(document: Document) -> Document:
    """Soft-delete a document by setting archived=True."""
    if not document.archived:
        document.archived = True
        document.save(update_fields=["archived"])
    return document


def unarchive_document(document: Document) -> Document:
    """Restore an archived document by setting archived=False."""
    if document.archived:
        document.archived = False
        document.save(update_fields=["archived"])
    return document


_PREVIEW_BLOCKED_SUFFIXES = (
    ".exe",
    ".dll",
    ".bat",
    ".cmd",
    ".msi",
    ".sh",
    ".ps1",
)


def _document_filename_candidates(document: Document) -> list[str]:
    return [
        n for n in (document.original_filename, document.name, document.file_key) if n
    ]


def effective_preview_mime(document: Document) -> str:
    raw = (document.mime_type or "").strip().lower()
    if raw:
        return raw
    for name in _document_filename_candidates(document):
        guessed, _ = mimetypes.guess_type(name)
        if guessed:
            return guessed.lower()
    return ""


def document_preview_blocked(document: Document) -> bool:
    for name in _document_filename_candidates(document):
        lower = name.lower()
        for suf in _PREVIEW_BLOCKED_SUFFIXES:
            if lower.endswith(suf):
                return True
    return False


def preview_response_content_type_override(document: Document) -> str | None:
    mime = effective_preview_mime(document)
    if mime == "application/pdf":
        return None
    if not any(
        n.lower().endswith(".pdf") for n in _document_filename_candidates(document)
    ):
        return None
    if mime in (
        "",
        "application/octet-stream",
        "binary/octet-stream",
        "application/x-download",
    ):
        return "application/pdf"
    return None


def build_document_inline_preview_url(document: Document) -> tuple[str, str | None]:
    if document_preview_blocked(document):
        return "", "Preview is not available for this file type."
    override = preview_response_content_type_override(document)
    url = generate_presigned_url(
        document.file_key,
        expiry_seconds=300,
        inline=True,
        response_content_type=override,
    )
    return url, None


# ──────────────────────────────────────────
# Bulk operations
# ──────────────────────────────────────────


def bulk_hard_delete(documents) -> None:
    """Transactionally hard-delete multiple documents and their storage files."""
    with transaction.atomic():
        for doc in documents:
            hard_delete_document(doc)


def bulk_archive(documents) -> None:
    """Soft-delete multiple documents in a single transaction."""
    with transaction.atomic():
        for doc in documents:
            archive_document(doc)


# ──────────────────────────────────────────
# ZIP download
# ──────────────────────────────────────────


def generate_zip_url(documents, user) -> str:
    """Package accessible documents into a ZIP and return a presigned URL."""
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            if not is_document_accessible(user, doc):
                continue
            try:
                raw = default_storage.open(doc.file_key).read()
                arcname = doc.original_filename or f"{doc.name}.bin"
                zf.writestr(arcname, raw)
            except Exception:
                continue

    zip_buffer.seek(0)
    now = timezone.now()
    zip_key = f"exports/bulk-download-{now:%Y%m%d-%H%M%S}.zip"
    default_storage.save(zip_key, ContentFile(zip_buffer.read()))

    return generate_presigned_url(zip_key, expiry_seconds=600)


# ──────────────────────────────────────────
# CSV export
# ──────────────────────────────────────────


def export_documents_csv(documents) -> str:
    """Write document metadata to CSV and return a presigned URL."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Name",
            "Category",
            "Signature Status",
            "Expiry Date",
            "Uploaded By",
            "Updated At",
            "Tags",
            "Confidential",
        ]
    )

    for doc in documents:
        writer.writerow(
            [
                doc.name,
                doc.category,
                doc.signature_status,
                doc.expiry_date.isoformat() if doc.expiry_date else "",
                uploader_display_name(doc.uploaded_by),
                doc.updated_at.isoformat(),
                ", ".join(doc.tags or []),
                "Yes" if doc.is_confidential else "No",
            ]
        )

    now = timezone.now()
    csv_key = f"exports/documents-{now:%Y%m%d-%H%M%S}.csv"
    default_storage.save(csv_key, ContentFile(buffer.getvalue().encode("utf-8")))

    return generate_presigned_url(csv_key, expiry_seconds=300)


# ──────────────────────────────────────────
# Version helpers
# ──────────────────────────────────────────


def _increment_version(current: str) -> str:
    """Bump the minor version number. '1.0' → '2.0', '2.1' → '3.0'."""
    try:
        major = int(current.split(".")[0])
        return f"{major + 1}.0"
    except (ValueError, IndexError):
        return "2.0"


def create_new_version(
    document: Document, file_key: str, file_size: int, uploaded_by, notes: str = ""
) -> DocumentVersion:
    """Record a new version for a document and bump its current_version."""
    new_version_str = _increment_version(document.current_version)
    version = DocumentVersion.objects.create(
        document=document,
        version=new_version_str,
        file_key=file_key,
        file_size=file_size,
        uploaded_by=uploaded_by,
        notes=notes,
    )
    document.current_version = new_version_str
    document.save(update_fields=["current_version"])
    return version
