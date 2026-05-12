from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from core.enums import DocumentSignatureStatus, DocumentSignerStatus
from core.models import Document, DocumentSignatureAuditLog, DocumentSigner
from core.permissions import _get_user_profile
from core.services.mail.signature_notifications import (
    notify_signature_reminder,
    notify_signature_requested,
)
from core.services.notification_service import (
    notify_signers_reminder as notify_signers_reminder_in_app,
)
from core.services.notification_service import (
    notify_signers_signature_requested,
)


class ActiveSignatureWorkflowError(Exception):
    """Raised when a request would replace an active workflow without semantics."""


def _request_meta(request_context) -> dict[str, str]:
    meta = getattr(request_context, "META", None) or {}
    forwarded_for = meta.get("HTTP_X_FORWARDED_FOR", "")
    ip_address = forwarded_for.split(",", 1)[0].strip() if forwarded_for else ""
    ip_address = ip_address or meta.get("REMOTE_ADDR", "") or ""
    return {
        "ip_address": ip_address,
        "user_agent": meta.get("HTTP_USER_AGENT", "") or "",
    }


def _actor_profile(actor):
    if actor is None:
        return None
    if hasattr(actor, "user") and not hasattr(actor, "is_authenticated"):
        return actor
    return _get_user_profile(actor)


def _audit(
    document, event, signer=None, actor=None, request_context=None, metadata=None
):
    meta = _request_meta(request_context)
    return DocumentSignatureAuditLog.objects.create(
        document=document,
        signer=signer,
        actor=_actor_profile(actor),
        event=event,
        ip_address=meta["ip_address"] or None,
        user_agent=meta["user_agent"],
        metadata=metadata or {},
    )


def _signature_hash(document, signer, signature_payload, signed_at):
    secret = getattr(settings, "DOCUMENT_SIGNATURE_SECRET", None) or settings.SECRET_KEY
    payload = {
        "document_id": document.pk,
        "signer_id": signer.pk,
        "signature_payload": signature_payload,
        "signed_at": signed_at.isoformat(),
        "file_key": document.file_key,
        "current_version": document.current_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(
        str(secret).encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def recompute_document_signature_status(document: Document) -> Document:
    signers = list(document.signers.all())
    previous_status = document.signature_status
    previous_signed_at = document.signed_at

    if not signers:
        document.signature_status = DocumentSignatureStatus.NOT_REQUIRED
        document.signed_at = None
    elif any(s.status == DocumentSignerStatus.REJECTED for s in signers):
        document.signature_status = DocumentSignatureStatus.REJECTED
        document.signed_at = None
    elif all(s.status == DocumentSignerStatus.SIGNED for s in signers):
        document.signature_status = DocumentSignatureStatus.SIGNED
        signed_times = [s.signed_at for s in signers if s.signed_at]
        document.signed_at = max(signed_times) if signed_times else timezone.now()
    else:
        document.signature_status = DocumentSignatureStatus.PENDING
        document.signed_at = None

    if (
        document.signature_status != previous_status
        or document.signed_at != previous_signed_at
    ):
        document.save(update_fields=["signature_status", "signed_at", "updated_at"])
    return document


def request_document_signatures(document, signers, requested_by, request_context=None):
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        if document.archived:
            raise serializers.ValidationError(
                "Archived documents cannot be sent for signature."
            )

        active_exists = (
            DocumentSigner.objects.select_for_update()
            .filter(
                document=document,
                status__in=[
                    DocumentSignerStatus.PENDING,
                    DocumentSignerStatus.NOT_SENT,
                ],
            )
            .exists()
        )
        if (
            document.signature_status == DocumentSignatureStatus.PENDING
            or active_exists
        ):
            raise ActiveSignatureWorkflowError(
                "A signature workflow is already active for this document."
            )

        requested_at = timezone.now()
        requested_by_profile = _actor_profile(requested_by)
        created = []
        DocumentSigner.objects.filter(document=document).delete()
        for entry in signers:
            signer = DocumentSigner.objects.create(
                document=document,
                name=entry["name"],
                email=entry["email"].lower(),
                status=DocumentSignerStatus.PENDING,
                requested_at=requested_at,
                requested_by=requested_by_profile,
            )
            created.append(signer)
            _audit(
                document,
                DocumentSignatureAuditLog.Event.REQUESTED,
                signer=signer,
                actor=requested_by,
                request_context=request_context,
                metadata={"signer_email": signer.email},
            )

        document.signature_status = DocumentSignatureStatus.PENDING
        document.signed_at = None
        document.save(update_fields=["signature_status", "signed_at", "updated_at"])

    requester_user = getattr(requested_by_profile, "user", None) or requested_by
    for signer in created:
        notify_signature_requested(document, signer, requester=requester_user)
    notify_signers_signature_requested(document, created)
    return document, created


def sign_document(document, signer, actor, signature_payload, request_context=None):
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        if document.archived:
            raise serializers.ValidationError("Archived documents cannot be signed.")

        signer = DocumentSigner.objects.select_for_update().get(
            pk=signer.pk,
            document=document,
        )
        if signer.status == DocumentSignerStatus.SIGNED:
            raise serializers.ValidationError("This signer has already signed.")
        if signer.status not in [
            DocumentSignerStatus.PENDING,
            DocumentSignerStatus.NOT_SENT,
        ]:
            raise serializers.ValidationError("This signer is not pending signature.")

        if signature_payload.get("accepted_terms") is not True:
            raise serializers.ValidationError(
                {"signature": "accepted_terms must be true."}
            )
        if not str(signature_payload.get("value", "")).strip():
            raise serializers.ValidationError(
                {"signature": "Signature value is required."}
            )

        now = timezone.now()
        request_meta = _request_meta(request_context)
        signer.status = DocumentSignerStatus.SIGNED
        signer.signed_at = now
        signer.signed_by = _actor_profile(actor)
        signer.signature_metadata = {
            "type": signature_payload.get("type"),
            "value": signature_payload.get("value"),
            "accepted_terms": True,
            "ip_address": request_meta["ip_address"],
            "user_agent": request_meta["user_agent"],
            "actor_user_id": getattr(actor, "id", None),
            "document_version": document.current_version,
            "file_key": document.file_key,
        }
        signer.signature_hash = _signature_hash(
            document, signer, signature_payload, now
        )
        signer.save(
            update_fields=[
                "status",
                "signed_at",
                "signed_by",
                "signature_metadata",
                "signature_hash",
            ]
        )

        _audit(
            document,
            DocumentSignatureAuditLog.Event.SIGNED,
            signer=signer,
            actor=actor,
            request_context=request_context,
            metadata={"signature_hash": signer.signature_hash},
        )
        recompute_document_signature_status(document)
        document.refresh_from_db()
        return document, signer


def reject_signature(document, signer, actor, reason, request_context=None):
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        if document.archived:
            raise serializers.ValidationError("Archived documents cannot be modified.")
        signer = DocumentSigner.objects.select_for_update().get(
            pk=signer.pk,
            document=document,
        )
        if signer.status == DocumentSignerStatus.SIGNED:
            raise serializers.ValidationError("Signed signatures cannot be rejected.")

        signer.status = DocumentSignerStatus.REJECTED
        signer.declined_at = timezone.now()
        signer.decline_reason = reason or ""
        signer.save(update_fields=["status", "declined_at", "decline_reason"])
        _audit(
            document,
            DocumentSignatureAuditLog.Event.REJECTED,
            signer=signer,
            actor=actor,
            request_context=request_context,
            metadata={"reason": signer.decline_reason},
        )
        recompute_document_signature_status(document)
        document.refresh_from_db()
        return document, signer


def reset_document_signatures(document, actor, request_context=None):
    """Testing helper — clear all signers and reset signature status."""
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        signers = list(DocumentSigner.objects.filter(document=document))
        for signer in signers:
            _audit(
                document,
                DocumentSignatureAuditLog.Event.REJECTED,
                signer=signer,
                actor=actor,
                request_context=request_context,
                metadata={"reason": "reset_for_testing"},
            )
        DocumentSigner.objects.filter(document=document).delete()
        document.signature_status = DocumentSignatureStatus.NOT_REQUIRED
        document.signed_at = None
        document.save(update_fields=["signature_status", "signed_at", "updated_at"])
        return document


def get_signature_audit_events(document):
    return DocumentSignatureAuditLog.objects.filter(document=document).select_related(
        "signer",
        "actor__user",
    )


def remind_pending_signers(document, actor, request_context=None):
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        pending = list(
            DocumentSigner.objects.select_for_update().filter(
                document=document,
                status__in=[
                    DocumentSignerStatus.PENDING,
                    DocumentSignerStatus.NOT_SENT,
                ],
            )
        )
        now = timezone.now()
        for signer in pending:
            signer.last_reminded_at = now
            signer.save(update_fields=["last_reminded_at"])
            _audit(
                document,
                DocumentSignatureAuditLog.Event.REMINDED,
                signer=signer,
                actor=actor,
                request_context=request_context,
            )

    actor_user = getattr(_actor_profile(actor), "user", None) or actor
    for signer in pending:
        notify_signature_reminder(document, signer, requester=actor_user)
    notify_signers_reminder_in_app(document, pending)
    return len(pending)
