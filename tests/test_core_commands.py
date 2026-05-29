import io
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command

from core.models import Permission, Role


def test_backfill_asset_qr_codes_dry_run(monkeypatch):
    from core.management.commands import backfill_asset_qr_codes as cmd_mod

    asset = type("AssetObj", (), {})()
    asset.pk = 1
    asset.qr_code_payload = "old"
    asset.qr_code_image = type("Img", (), {"name": "old.png"})()
    asset2 = type("AssetObj", (), {})()
    asset2.pk = 2
    asset2.qr_code_payload = "payload-2"
    asset2.qr_code_image = type("Img", (), {"name": "image-2.png"})()

    monkeypatch.setattr(
        cmd_mod.Asset.objects, "order_by", lambda *a, **k: [asset, asset2]
    )
    monkeypatch.setattr(
        cmd_mod, "build_asset_qr_payload", lambda asset: f"payload-{asset.pk}"
    )
    monkeypatch.setattr(
        cmd_mod, "build_asset_qr_image_path", lambda asset: f"image-{asset.pk}.png"
    )
    called = []
    monkeypatch.setattr(
        cmd_mod,
        "ensure_asset_qr_code",
        lambda asset, regenerate_image=False: called.append(
            (asset.pk, regenerate_image)
        ),
    )

    out = io.StringIO()
    call_command("backfill_asset_qr_codes", dry_run=True, stdout=out)
    assert "would update 1 asset QR codes" in out.getvalue()
    assert called == []


@pytest.mark.django_db
def test_load_permissions_and_role_permissions(tmp_path):
    perm_csv = tmp_path / "permissions.csv"
    perm_csv.write_text(
        "module_name,feature_action\nDocuments,view\nDocuments,edit\n",
        encoding="utf-8",
    )
    out = io.StringIO()
    call_command("load_permissions", str(perm_csv), stdout=out)
    assert "Finished loading permissions" in out.getvalue()
    assert Permission.objects.filter(module_name="Documents").count() == 2

    role_csv = tmp_path / "role_permissions.csv"
    role_csv.write_text(
        "\n".join(
            [
                "role_id,module_name,feature_action,permission,operation_type",
                "EDITOR,Documents,view,YES,add",
                "EDITOR,Documents,edit,NO,merge",
                "EDITOR,Documents,edit,YES,merge",
                "ADMIN,Documents,view,YES,override",
            ]
        ),
        encoding="utf-8",
    )
    out = io.StringIO()
    call_command("load_role_permissions", str(role_csv), stdout=out)
    editor = Role.objects.get(name="EDITOR")
    admin = Role.objects.get(name="ADMIN")
    assert editor.permissions.filter(module_name="Documents").exists()
    assert admin.permissions.filter(module_name="Documents").exists()
    assert "Finished loading role permissions" in out.getvalue()


def test_materialize_review_reminders_command(monkeypatch):
    from core.management.commands import materialize_review_reminders as cmd_mod

    monkeypatch.setattr(
        cmd_mod,
        "materialize_performance_review_reminders",
        lambda actor=None: {
            "reviews_processed": 3,
            "created_count": 2,
            "sent_count": 1,
        },
    )
    out = io.StringIO()
    call_command("materialize_review_reminders", stdout=out)
    assert (
        "Processed 3 reviews, created 2 reminders, dispatched 1 reminders."
        in out.getvalue()
    )


def test_setup_public_tenant_noop(monkeypatch):
    from django.conf import settings as django_settings

    monkeypatch.setattr(django_settings, "USE_TENANTS", False, raising=False)
    out = io.StringIO()
    call_command("setup_public_tenant", stdout=out)
    assert "Tenants disabled; skipping setup_public_tenant." in out.getvalue()


@pytest.mark.django_db
def test_setup_super_admin_creates_user_and_role():
    Permission.objects.create(module_name="Documents", feature_action="view")
    out = io.StringIO()
    call_command(
        "setup_super_admin",
        username="admin-test",
        email="admin-test@example.com",
        password="secret123",
        create_user=True,
        stdout=out,
    )
    user = User.objects.get(username="admin-test")
    assert user.is_superuser is True and user.is_staff is True
    profile = user.profile
    assert profile.role.name == "SUPER_ADMIN"
    assert "Assigned SUPER_ADMIN" in out.getvalue()


def test_runserver_defaults_to_localhost():
    from core.management.commands.runserver import Command

    assert Command.default_addr == "localhost"
    assert "localhost:8000" in Command.help


def test_sync_media_to_storage_copies_and_deletes_local_files(monkeypatch, tmp_path):
    from core.management.commands import sync_media_to_storage as cmd_mod

    media_root = tmp_path / "media"
    docs_dir = media_root / "documents"
    docs_dir.mkdir(parents=True)
    local_file = docs_dir / "contract.txt"
    local_file.write_text("signed", encoding="utf-8")

    class FakeStorage:
        def __init__(self):
            self.saved = []

        def exists(self, key):
            return False

        def save(self, key, file_obj):
            self.saved.append((key, file_obj.read()))
            return f"remote/{key}"

    storage = FakeStorage()
    monkeypatch.setattr(cmd_mod.settings, "MEDIA_ROOT", media_root)
    monkeypatch.setattr(cmd_mod, "default_storage", storage)

    out = io.StringIO()
    call_command(
        "sync_media_to_storage",
        prefix="documents",
        delete_local=True,
        stdout=out,
    )

    assert storage.saved == [("documents/contract.txt", b"signed")]
    assert not local_file.exists()
    assert "Synced 1 file(s), skipped 0, deleted 1 local file(s)." in out.getvalue()


def test_sync_media_to_storage_dry_run_reports_existing_keys(monkeypatch, tmp_path):
    from core.management.commands import sync_media_to_storage as cmd_mod

    media_root = tmp_path / "media"
    avatars_dir = media_root / "avatars"
    avatars_dir.mkdir(parents=True)
    (avatars_dir / "avatar.png").write_bytes(b"png")

    class FakeStorage:
        def exists(self, key):
            return key == "avatars/avatar.png"

    monkeypatch.setattr(cmd_mod.settings, "MEDIA_ROOT", media_root)
    monkeypatch.setattr(cmd_mod, "default_storage", FakeStorage())

    out = io.StringIO()
    call_command("sync_media_to_storage", prefix="avatars", dry_run=True, stdout=out)

    output = out.getvalue()
    assert "skip-existing: avatars/avatar.png" in output
    assert "Dry run complete." in output


def test_sync_media_to_storage_overwrites_existing_keys(monkeypatch, tmp_path):
    from core.management.commands import sync_media_to_storage as cmd_mod

    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "existing.txt").write_text("new", encoding="utf-8")

    class FakeStorage:
        def __init__(self):
            self.deleted = []
            self.saved = []

        def exists(self, key):
            return True

        def delete(self, key):
            self.deleted.append(key)

        def save(self, key, file_obj):
            self.saved.append((key, file_obj.read()))
            return key

    storage = FakeStorage()
    monkeypatch.setattr(cmd_mod.settings, "MEDIA_ROOT", media_root)
    monkeypatch.setattr(cmd_mod, "default_storage", storage)

    out = io.StringIO()
    call_command("sync_media_to_storage", overwrite=True, stdout=out)

    assert storage.deleted == ["existing.txt"]
    assert storage.saved == [("existing.txt", b"new")]
    assert "Copied: existing.txt -> existing.txt" in out.getvalue()


def test_upload_avatars_to_r2_requires_scope():
    with pytest.raises(ValueError, match="Use --all or --user-id"):
        call_command("upload_avatars_to_r2")


def test_upload_avatars_to_r2_uploads_matching_local_avatar(monkeypatch, tmp_path):
    from core.management.commands import upload_avatars_to_r2 as cmd_mod

    media_root = tmp_path / "media"
    avatar_path = media_root / "avatars" / "user.png"
    avatar_path.parent.mkdir(parents=True)
    avatar_path.write_bytes(b"avatar-bytes")

    class FakeStorage:
        def __init__(self):
            self.saved = []

        def save(self, name, content):
            self.saved.append((name, content.read()))
            return f"remote/{name}"

    storage = FakeStorage()
    avatar = SimpleNamespace(name="avatars/user.png", storage=storage)
    profile = SimpleNamespace(user_id=7, avatar=avatar)

    class FakeQuerySet:
        def __init__(self, profiles):
            self.profiles = profiles
            self.filtered_user_id = None

        def all(self):
            return self

        def filter(self, **kwargs):
            self.filtered_user_id = kwargs["user_id"]
            return self

        def iterator(self):
            return iter(self.profiles)

    profiles = FakeQuerySet([profile])
    monkeypatch.setattr(cmd_mod.settings, "MEDIA_ROOT", media_root)
    monkeypatch.setattr(cmd_mod, "UserProfile", SimpleNamespace(objects=profiles))

    out = io.StringIO()
    call_command("upload_avatars_to_r2", user_id=7, delete_local=True, stdout=out)

    assert profiles.filtered_user_id == 7
    assert storage.saved == [("avatars/user.png", b"avatar-bytes")]
    assert not avatar_path.exists()
    assert "Uploaded user_id=7 -> remote/avatars/user.png" in out.getvalue()
    assert "Uploaded 1 avatar(s)." in out.getvalue()


def test_upload_avatars_to_r2_skips_missing_avatar_file(monkeypatch, tmp_path):
    from core.management.commands import upload_avatars_to_r2 as cmd_mod

    avatar = SimpleNamespace(name="avatars/missing.png")
    profile = SimpleNamespace(user_id=11, avatar=avatar)

    class FakeQuerySet:
        def all(self):
            return self

        def iterator(self):
            return iter([profile])

    monkeypatch.setattr(cmd_mod.settings, "MEDIA_ROOT", tmp_path)
    monkeypatch.setattr(cmd_mod, "UserProfile", SimpleNamespace(objects=FakeQuerySet()))

    out = io.StringIO()
    call_command("upload_avatars_to_r2", all=True, stdout=out)

    output = out.getvalue()
    assert "Skipping user_id=11: local file missing:" in output
    assert "Uploaded 0 avatar(s)." in output
