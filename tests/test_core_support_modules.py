import importlib
import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from PIL import Image
from rest_framework.exceptions import NotFound

from core.avatar_utils import _pick_colors, generate_initials_avatar_png, get_initials
from core.enums import TemplateStatus, TemplateVisibility
from core.models import (
    DocumentTemplate,
    Permission,
    Role,
    TemplateField,
)
from core.services.document_signature_permissions import (
    can_initiate_signature_request,
    can_send_signature_reminder,
    can_sign_for,
)
from core.utils import (
    apply_profile_updates_and_save,
    clone_template,
    download_and_save_avatar,
    generate_secure_password,
    generate_unique_username,
    get_role_permissions_bitmap,
    get_template_or_404,
    normalize_enum_like,
    normalize_iso_date,
    normalize_manager_ids,
    normalize_trimmed_string,
    resolve_template_content,
    upgrade_google_picture_url,
    validate_template_fields,
    verify_google_id_token,
)


def _reload_config_settings(monkeypatch, **env):
    import dotenv

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DEV_DATABASE_URL", raising=False)
    monkeypatch.delenv("PROD_DATABASE_URL", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    import config.settings as config_settings

    return importlib.reload(config_settings)


def test_config_settings_local_sqlite_branch(monkeypatch):
    settings_mod = _reload_config_settings(monkeypatch, ENVIRONMENT="local")
    assert settings_mod.USE_TENANTS is False
    assert settings_mod.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert settings_mod.INSTALLED_APPS[0] == "django.contrib.admin"


def test_config_settings_postgres_tenant_branch(monkeypatch):
    settings_mod = _reload_config_settings(
        monkeypatch,
        ENVIRONMENT="prod",
        DATABASE_URL="postgres://user:pass@localhost:5432/bloomhub",
        SITE_URL="https://example.com",
    )
    assert settings_mod.USE_TENANTS is True
    assert (
        settings_mod.DATABASES["default"]["ENGINE"]
        == "django_tenants.postgresql_backend"
    )
    assert "django_tenants" in settings_mod.INSTALLED_APPS


def test_r2_storage_connection_paths(monkeypatch):
    from django.conf import settings as django_settings

    from config.storage import R2Storage

    monkeypatch.setattr(django_settings, "AWS_S3_VERIFY", False, raising=False)

    class FakeSession:
        def __init__(self):
            self.calls = []

        def resource(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"args": args, "kwargs": kwargs}

    class FakeConfig:
        def merge(self, other):
            return "merged-config"

    storage = R2Storage.__new__(R2Storage)
    storage.verify = "explicit-verify"
    storage._connections = SimpleNamespace(connection=None)
    storage._unsigned_connections = SimpleNamespace(connection=None)
    storage.region_name = "auto"
    storage.use_ssl = True
    storage.endpoint_url = "https://example.test"
    storage.client_config = FakeConfig()
    session = FakeSession()
    monkeypatch.setattr(storage, "_create_session", lambda: session)

    assert storage._get_verify() is False
    conn = storage.connection
    assert conn["kwargs"]["verify"] is False
    assert storage.connection is conn

    unsigned = storage.unsigned_connection
    assert unsigned["kwargs"]["verify"] is False
    assert unsigned["kwargs"]["config"] == "merged-config"


def test_get_initials_and_colors():
    assert get_initials("Hanan Bajramovic", None) == "HB"
    assert get_initials("Madonna", None) == "MA"
    assert get_initials(None, "user.name") == "UN"
    assert get_initials("", "") == "U"
    assert _pick_colors("seed") == _pick_colors("seed")


def test_generate_initials_avatar_png_fallback_branch(monkeypatch):
    png = generate_initials_avatar_png("AB", size=64, seed="seed")
    img = Image.open(io.BytesIO(png))
    assert img.size == (64, 64)
    assert img.format == "PNG"


def test_generate_secure_password_length():
    password = generate_secure_password(24)
    assert len(password) == 24
    assert password.isalnum()


@pytest.mark.django_db
def test_generate_unique_username_and_permissions_bitmap():
    User.objects.create_user(username="alex", password="x")
    assert generate_unique_username("alex@example.com") == "alex1"

    role = Role.objects.create(name="Reviewer")
    perm = Permission.objects.create(module_name="Documents", feature_action="review")
    role.permissions.add(perm)

    user = User.objects.create_user(
        username="jane", email="jane@example.com", password="x"
    )
    profile = user.profile
    profile.role = role
    profile.permissions = ""
    profile.save(update_fields=["role", "permissions"])

    assert get_role_permissions_bitmap(role) == bin(1 << perm.bit_position)[2:]
    assert profile.has_permission(perm) is True

    apply_profile_updates_and_save(profile, {"full_name": "Jane Doe"})
    profile.refresh_from_db()
    assert profile.full_name == "Jane Doe"


def test_verify_google_id_token(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-123")
    calls = {}

    def fake_verify(token, request, client_id=None):
        calls["token"] = token
        calls["client_id"] = client_id
        return {"sub": "123"}

    from google.oauth2 import id_token

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    assert verify_google_id_token("token-abc") == {"sub": "123"}
    assert calls == {"token": "token-abc", "client_id": "client-123"}


def test_upgrade_google_picture_url_and_normalizers():
    assert (
        upgrade_google_picture_url("https://x/photo=s96-c", 400)
        == "https://x/photo=s400-c"
    )
    assert (
        upgrade_google_picture_url("https://x/photo", 400) == "https://x/photo=s400-c"
    )
    assert normalize_trimmed_string("  hi  ") == "hi"
    assert normalize_trimmed_string("   ") is None
    assert normalize_enum_like("  ABC  ") == "abc"
    assert normalize_iso_date(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02"
    assert normalize_manager_ids([1, "2", SimpleNamespace(user_id=2), 0, -1, None]) == [
        1,
        2,
    ]


def test_template_helpers_and_download_avatar(monkeypatch):
    assert resolve_template_content("Hi {{name}}", {"name": "Bob"}) == "Hi Bob"

    fields = [
        SimpleNamespace(field_key="name", label="Name", is_required=True),
        SimpleNamespace(field_key="title", label="Title", is_required=False),
    ]
    assert validate_template_fields(fields, {"name": ""}) == ["Name"]

    class Avatar:
        def __init__(self):
            self.saved = None

        def save(self, name, content, save=True):
            self.saved = (name, content.read(), save)

    profile = SimpleNamespace(user_id=7, avatar=Avatar())
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=10: io.BytesIO(b"png-bytes")
    )
    assert download_and_save_avatar(profile, "https://example.com/photo=s96-c") is True
    assert profile.avatar.saved[0] == "avatar.png"

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert download_and_save_avatar(profile, "https://example.com/photo") is False


@pytest.mark.django_db
def test_clone_template_and_get_template_or_404():
    user = User.objects.create_user(username="template-owner", password="x")
    template = DocumentTemplate.objects.create(
        name="Offer",
        description="desc",
        category="contracts",
        content="Hello {{name}}",
        visibility=TemplateVisibility.SHARED,
        status=TemplateStatus.PUBLISHED,
        is_system_template=True,
        created_by=user.profile,
    )
    TemplateField.objects.create(
        template=template,
        label="Name",
        field_key="name",
        is_required=True,
        order=2,
        options='["A"]',
    )
    TemplateField.objects.create(
        template=template,
        label="Role",
        field_key="role",
        is_required=False,
        order=1,
    )

    cloned = clone_template(template, user.profile)
    assert cloned.name == "Copy of Offer"
    assert cloned.visibility == TemplateVisibility.PRIVATE
    assert cloned.is_system_template is False
    assert [field.field_key for field in cloned.fields.all()] == ["role", "name"]

    assert get_template_or_404(cloned.pk).pk == cloned.pk
    with pytest.raises(NotFound):
        get_template_or_404(999999)


def test_document_signature_permission_helpers(monkeypatch):
    class UserObj(SimpleNamespace):
        pass

    user = UserObj(
        is_authenticated=True,
        is_staff=False,
        is_superuser=False,
        email="owner@example.com",
    )
    signer = SimpleNamespace(email="owner@example.com")

    monkeypatch.setattr(
        "core.services.document_signature_permissions.is_hr_or_admin",
        lambda u: False,
    )
    monkeypatch.setattr(
        "core.services.document_signature_permissions.has_document_permission",
        lambda u, action: action == "sign_documents",
    )
    assert can_initiate_signature_request(user) is False
    assert can_send_signature_reminder(user) is False
    assert (
        can_sign_for(UserObj(is_authenticated=False, email="owner@example.com"), signer)
        is False
    )
    assert can_sign_for(user, signer) is True
    assert (
        can_sign_for(UserObj(is_authenticated=True, email="other@example.com"), signer)
        is True
    )
