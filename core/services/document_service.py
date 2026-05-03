from __future__ import annotations

import csv
import io
import mimetypes
import zipfile

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from core.enums import DocumentAccessRole
from core.models import Document, DocumentVersion
from core.utils import generate_presigned_url, uploader_display_name

# Backward-compat alias so callers that import DocumentRole from this module
# continue to work while the codebase migrates to DocumentAccessRole.
DocumentRole = DocumentAccessRole


# ──────────────────────────────────────────
# Role resolution
# ──────────────────────────────────────────


def resolve_document_role(user) -> DocumentAccessRole:
    """Determine the effective document access role for a user."""
    if not getattr(user, "is_authenticated", False):
        return DocumentRole.EMPLOYEE

    if getattr(user, "is_superuser", False):
        return DocumentRole.ADMIN

    if getattr(user, "is_staff", False):
        return DocumentRole.HR

    profile = getattr(user, "profile", None)
    role_name = (getattr(getattr(profile, "role", None), "name", "") or "").lower()

    if role_name == "admin":
        return DocumentRole.ADMIN
    if role_name.startswith("hr"):
        return DocumentRole.HR
    return DocumentRole.EMPLOYEE


def is_hr_or_admin(user) -> bool:
    """Return True when user has HR-level or admin-level document access."""
    return resolve_document_role(user) in (DocumentRole.HR, DocumentRole.ADMIN)


# ──────────────────────────────────────────
# Access control
# ──────────────────────────────────────────


def is_document_accessible(user, document: Document) -> bool:
    """Check whether a user can access a specific document."""
    role = resolve_document_role(user)

    if role == DocumentRole.ADMIN:
        return True

    if document.is_confidential:
        return False

    allowed = set(document.allowed_roles or [])
    if role == DocumentRole.HR:
        return bool(allowed & {DocumentRole.HR.value, DocumentRole.EMPLOYEE.value})

    return DocumentRole.EMPLOYEE.value in allowed


def filter_accessible_documents(user, queryset):
    """Return only the subset of the queryset the user is allowed to see."""
    role = resolve_document_role(user)

    if role == DocumentRole.ADMIN:
        return queryset

    non_confidential = queryset.filter(is_confidential=False)

    if role == DocumentRole.HR:
        return [
            doc
            for doc in non_confidential
            if set(doc.allowed_roles or [])
            & {DocumentRole.HR.value, DocumentRole.EMPLOYEE.value}
        ]

    return [
        doc
        for doc in non_confidential
        if DocumentRole.EMPLOYEE.value in (doc.allowed_roles or [])
    ]


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
