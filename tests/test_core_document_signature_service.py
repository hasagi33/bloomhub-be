from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import serializers

from core.enums import DocumentSignatureStatus, DocumentSignerStatus
from core.models import Document, DocumentSignatureAuditLog, DocumentSigner
from core.services import document_signature_service as sig


def test_signature_helpers_and_status_recompute():
    request_context = SimpleNamespace(
        META={"HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8", "HTTP_USER_AGENT": "UA"}
    )
    assert sig._request_meta(request_context) == {
        "ip_address": "1.2.3.4",
        "user_agent": "UA",
    }
    assert sig._actor_profile(SimpleNamespace(user=SimpleNamespace())) is not None
    assert sig._actor_profile(None) is None

    document = SimpleNamespace(pk=1, file_key="f", current_version="1.0")
    signer = SimpleNamespace(pk=2)
    assert sig._signature_hash(
        document, signer, {"value": "x"}, timezone.now()
    )  # deterministic hash

    class FakeSigner:
        def __init__(self, status, signed_at=None):
            self.status = status
            self.signed_at = signed_at

    class FakeDoc:
        def __init__(self, signers):
            self.signers = SimpleNamespace(all=lambda: signers)
            self.signature_status = DocumentSignatureStatus.PENDING
            self.signed_at = None
            self.saved = []

        def save(self, update_fields=None):
            self.saved.append(update_fields)

    doc = FakeDoc([])
    sig.recompute_document_signature_status(doc)
    assert doc.signature_status == DocumentSignatureStatus.NOT_REQUIRED

    doc = FakeDoc([FakeSigner(DocumentSignerStatus.REJECTED)])
    sig.recompute_document_signature_status(doc)
    assert doc.signature_status == DocumentSignatureStatus.REJECTED

    now = timezone.now()
    doc = FakeDoc(
        [
            FakeSigner(DocumentSignerStatus.SIGNED, now),
            FakeSigner(DocumentSignerStatus.SIGNED, now),
        ]
    )
    sig.recompute_document_signature_status(doc)
    assert doc.signature_status == DocumentSignatureStatus.SIGNED

    doc = FakeDoc([FakeSigner(DocumentSignerStatus.PENDING)])
    sig.recompute_document_signature_status(doc)
    assert doc.signature_status == DocumentSignatureStatus.PENDING


@pytest.mark.django_db
def test_request_sign_sign_reject_reset_and_remind(monkeypatch):
    owner = User.objects.create_user(
        username="owner", email="owner@example.com", password="x"
    )
    actor = owner.profile
    document = Document.objects.create(
        name="Contract",
        file_key="docs/contract.pdf",
        category="contracts",
        uploaded_by=actor,
    )

    monkeypatch.setattr(sig, "notify_signature_requested", lambda *a, **k: None)
    monkeypatch.setattr(sig, "notify_signature_reminder", lambda *a, **k: None)
    monkeypatch.setattr(sig, "notify_signers_signature_requested", lambda *a, **k: None)
    monkeypatch.setattr(sig, "notify_signers_reminder_in_app", lambda *a, **k: None)

    doc, created = sig.request_document_signatures(
        document,
        [{"name": "Signer One", "email": "one@example.com"}],
        requested_by=actor,
        request_context=SimpleNamespace(META={"REMOTE_ADDR": "127.0.0.1"}),
    )
    assert doc.signature_status == DocumentSignatureStatus.PENDING
    assert len(created) == 1
    assert DocumentSignatureAuditLog.objects.filter(document=document).count() == 1

    signed_doc, signer = sig.sign_document(
        document,
        created[0],
        actor,
        {"accepted_terms": True, "value": "Jane Doe", "type": "drawn"},
        request_context=SimpleNamespace(META={"HTTP_USER_AGENT": "UA"}),
    )
    assert signed_doc.signature_status == DocumentSignatureStatus.SIGNED
    assert signer.status == DocumentSignerStatus.SIGNED

    doc2 = Document.objects.create(
        name="Policy",
        file_key="docs/policy.pdf",
        category="policies",
        uploaded_by=actor,
    )
    doc2, created2 = sig.request_document_signatures(
        doc2,
        [{"name": "Signer Two", "email": "two@example.com"}],
        requested_by=actor,
    )
    rejected_doc, rejected_signer = sig.reject_signature(doc2, created2[0], actor, "no")
    assert rejected_doc.signature_status == DocumentSignatureStatus.REJECTED
    assert rejected_signer.status == DocumentSignerStatus.REJECTED

    doc3 = Document.objects.create(
        name="Reset",
        file_key="docs/reset.pdf",
        category="policies",
        uploaded_by=actor,
    )
    sig.request_document_signatures(
        doc3,
        [
            {"name": "Signer Three", "email": "three@example.com"},
            {"name": "Signer Four", "email": "four@example.com"},
        ],
        requested_by=actor,
    )
    pending_count = sig.remind_pending_signers(doc3, actor)
    assert pending_count == 2
    assert DocumentSignatureAuditLog.objects.filter(document=doc3).count() >= 2

    reset_doc = sig.reset_document_signatures(doc3, actor)
    assert reset_doc.signature_status == DocumentSignatureStatus.NOT_REQUIRED
    assert DocumentSigner.objects.filter(document=doc3).count() == 0

    document.archived = True
    document.save(update_fields=["archived"])
    with pytest.raises(serializers.ValidationError):
        sig.request_document_signatures(
            document, [{"name": "x", "email": "x@example.com"}], actor
        )
