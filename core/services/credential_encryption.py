from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core import signing


def _fernet() -> Fernet:
    key_source = (
        getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    )
    digest = hashlib.sha256(str(key_source).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str, *, legacy_salt: str = "") -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        if legacy_salt:
            return signing.loads(value, salt=legacy_salt)
        raise
