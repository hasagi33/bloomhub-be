import io
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

from core.enums import DocumentAccessRole, DocumentSignatureStatus
from core.models import Document, DocumentCategoryDefault, DocumentVersion, Permission
from core.services import document_service


def test_document_access_helpers(monkeypatch):
    class Query:
        def __init__(self, exists=False):
            self._exists = exists

        def exists(self):
            return self._exists

    profile = SimpleNamespace(
        pk=1,
        role=SimpleNamespace(name="HR"),
        email_address="p@example.com",
        direct_reports=Query(True),
    )
    user = SimpleNamespace(
        is_authenticated=True,
        is_staff=False,
        is_superuser=False,
        profile=profile,
        email="u@example.com",
    )
    doc = SimpleNamespace(
        allowed_roles=[DocumentAccessRole.HR.value],
        visibility_scope="roles",
        uploaded_by_id=1,
        employee_id=None,
        signers=SimpleNamespace(filter=lambda **kwargs: Query(False)),
    )

    monkeypatch.setattr(document_service, "_get_user_profile", lambda u: profile)
    assert document_service._profile_role_name(profile) == "hr"
    assert document_service.is_user_manager(user) is True
    assert document_service.resolve_document_role(user) == DocumentAccessRole.HR
    assert document_service.is_hr_or_admin(user) is True
    monkeypatch.setattr(
        document_service.Permission.objects,
        "get",
        lambda **kwargs: Permission(module_name="Documents", feature_action="x"),
    )
    profile.has_permission = lambda perm: True
    assert document_service.has_document_permission(user, "x") is True
    assert document_service.is_user_signer_of(user, doc) is False
    assert document_service.is_document_accessible(user, doc) is True

    doc.visibility_scope = SimpleNamespace(
        ONLY_ME="only_me", PROJECT_GROUP="project_group", ROLES="roles"
    ).ONLY_ME
    assert document_service.is_document_accessible(user, doc) is True


@pytest.mark.django_db
def test_document_category_defaults_and_versioning(monkeypatch):
    user = User.objects.create_user(username="doc", password="x")
    doc = Document.objects.create(
        name="Policy",
        file_key="docs/policy.pdf",
        category="policies",
        signature_status=DocumentSignatureStatus.NOT_REQUIRED,
        uploaded_by=user.profile,
    )
    DocumentCategoryDefault.objects.update_or_create(
        category="policies",
        defaults={"allowed_roles": [DocumentAccessRole.HR.value]},
    )

    defaults = document_service.get_document_category_defaults()
    assert defaults["policies"] == [DocumentAccessRole.HR.value]

    row = document_service.set_document_category_default(
        "contracts", [DocumentAccessRole.EMPLOYEE.value]
    )
    assert row.allowed_roles == [DocumentAccessRole.EMPLOYEE.value]

    document_service.update_document_visibility(
        doc, [DocumentAccessRole.HR.value], "only_me"
    )
    doc.refresh_from_db()
    assert doc.visibility_scope == "only_me"

    assert (
        document_service.effective_preview_mime(
            SimpleNamespace(
                mime_type="", original_filename="file.pdf", name="", file_key=""
            )
        )
        == "application/pdf"
    )
    assert (
        document_service.document_preview_blocked(
            SimpleNamespace(original_filename="run.exe", name="", file_key="")
        )
        is True
    )
    assert (
        document_service.preview_response_content_type_override(
            SimpleNamespace(
                mime_type="application/octet-stream",
                original_filename="file.pdf",
                name="",
                file_key="",
            )
        )
        == "application/pdf"
    )

    monkeypatch.setattr(
        document_service, "generate_presigned_url", lambda *a, **k: "https://signed"
    )
    url, error = document_service.build_document_inline_preview_url(
        SimpleNamespace(
            file_key="docs/policy.pdf",
            original_filename="policy.pdf",
            name="Policy",
            mime_type="application/pdf",
        )
    )
    assert url == "https://signed" and error is None

    version = document_service.create_new_version(
        doc, "docs/policy-v2.pdf", 123, user.profile, "note"
    )
    assert version.version == "2.0"
    assert DocumentVersion.objects.filter(document=doc).count() == 1


def test_document_export_and_bulk_operations(monkeypatch):
    user = SimpleNamespace(
        profile=SimpleNamespace(
            full_name="Alice Example",
            user=SimpleNamespace(get_full_name=lambda: "Alice Example"),
        )
    )
    docs = [
        SimpleNamespace(
            name="Doc1",
            category="policies",
            signature_status="signed",
            expiry_date=None,
            uploaded_by=user.profile,
            updated_at=date(2026, 1, 1),
            tags=["one", "two"],
            is_confidential=False,
            file_key="doc1.txt",
            original_filename="doc1.txt",
        ),
        SimpleNamespace(
            name="Doc2",
            category="policies",
            signature_status="signed",
            expiry_date=None,
            uploaded_by=user.profile,
            updated_at=date(2026, 1, 1),
            tags=[],
            is_confidential=True,
            file_key="doc2.txt",
            original_filename="doc2.txt",
        ),
    ]

    monkeypatch.setattr(
        document_service, "uploader_display_name", lambda profile: "Alice Example"
    )
    monkeypatch.setattr(
        document_service.default_storage, "save", lambda key, content: key
    )
    monkeypatch.setattr(
        document_service,
        "generate_presigned_url",
        lambda key, expiry_seconds=300: f"https://signed/{key}",
    )
    csv_url = document_service.export_documents_csv(docs)
    assert csv_url.startswith("https://signed/")

    storage_files = {}

    class FakeFile(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        document_service.default_storage, "open", lambda key: FakeFile(b"content")
    )
    monkeypatch.setattr(
        document_service.default_storage,
        "save",
        lambda key, content: storage_files.setdefault(key, content.read()) or key,
    )
    monkeypatch.setattr(
        document_service, "is_document_accessible", lambda user, doc: doc.name == "Doc1"
    )
    monkeypatch.setattr(
        document_service,
        "generate_presigned_url",
        lambda key, expiry_seconds=600: f"https://zip/{key}",
    )
    zip_url = document_service.generate_zip_url(docs, user)
    assert zip_url.startswith("https://zip/")
    assert storage_files

    deleted = []
    archived = []
    monkeypatch.setattr(
        document_service, "hard_delete_document", lambda doc: deleted.append(doc.name)
    )
    monkeypatch.setattr(
        document_service, "archive_document", lambda doc: archived.append(doc.name)
    )
    monkeypatch.setattr(document_service.transaction, "atomic", lambda: nullcontext())
    document_service.bulk_hard_delete(docs)
    document_service.bulk_archive(docs)
    assert deleted == ["Doc1", "Doc2"]
    assert archived == ["Doc1", "Doc2"]
