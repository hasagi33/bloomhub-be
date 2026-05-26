import io

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
