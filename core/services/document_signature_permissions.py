from __future__ import annotations

from core.services.document_service import has_document_permission, is_hr_or_admin


def can_initiate_signature_request(user) -> bool:
    return is_hr_or_admin(user) or has_document_permission(
        user,
        "initiate_signature_requests",
    )


def can_send_signature_reminder(user) -> bool:
    return can_initiate_signature_request(user)


def can_sign_for(user, signer) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    if is_hr_or_admin(user) or has_document_permission(user, "sign_documents"):
        return True
    return (getattr(user, "email", "") or "").lower() == signer.email.lower()
