from __future__ import annotations

import argparse
import io
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone
from PIL import ImageDraw, ImageFont

from core.avatar_utils import generate_initials_avatar_png, get_initials
from core.models import Permission, Role, TrainingBudget, TrainingEntry
from core.services.training_budget_service import (
    _maybe_notify_threshold,
    _resolve_hr_recipients,
    get_or_create_budget,
    get_remaining_for_year,
    recalculate_budget,
)


def test_entrypoint_and_tenant_modules_import_cleanly():
    import importlib

    import config.asgi as config_asgi
    import config.wsgi as config_wsgi

    tenants_apps = importlib.import_module("tenants.apps")

    with override_settings(
        TENANT_MODEL="tenants.Client",
        TENANT_DOMAIN_MODEL="tenants.Domain",
    ):
        import django.contrib.admin as django_admin
        import django_tenants.models as tenants_mixins

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(tenants_mixins, "TenantMixin", type("TenantMixin", (), {}))
        monkeypatch.setattr(tenants_mixins, "DomainMixin", type("DomainMixin", (), {}))
        monkeypatch.setattr(
            django_admin,
            "register",
            lambda *args, **kwargs: (lambda cls: cls),
        )
        tenants_models = importlib.import_module("tenants.models")
        tenants_admin = importlib.import_module("tenants.admin")
        monkeypatch.undo()

    assert config_asgi.application is not None
    assert config_wsgi.application is not None
    assert tenants_apps.TenantsConfig.name == "tenants"
    assert tenants_apps.TenantsConfig.verbose_name == "Tenants"
    assert tenants_admin.ClientAdmin.list_display == (
        "name",
        "schema_name",
        "created_on",
    )
    assert tenants_admin.DomainAdmin.list_display == ("domain", "tenant", "is_primary")
    assert tenants_models.Client.auto_create_schema is True
    assert tenants_models.Client.auto_drop_schema is False


def test_setup_public_tenant_command_branches(monkeypatch):
    import tenants.management.commands.setup_public_tenant as cmd_mod

    class FakeQuerySet:
        def __init__(self, rows, key=None, value=None):
            self._rows = list(rows)
            self._key = key
            self._value = value

        def exists(self):
            if self._key is None:
                return bool(self._rows)
            return any(getattr(row, self._key) == self._value for row in self._rows)

    class FakeTenant:
        objects = None
        saved = []

        def __init__(self, schema_name, name):
            self.schema_name = schema_name
            self.name = name

        def save(self):
            FakeTenant.saved.append(self)
            FakeTenant.objects.rows.append(self)

    class FakeDomain:
        objects = None
        created = []

        def __init__(self, domain, tenant, is_primary):
            self.domain = domain
            self.tenant = tenant
            self.is_primary = is_primary

    class TenantManager:
        def __init__(self):
            self.rows = []

        def filter(self, **kwargs):
            key, value = next(iter(kwargs.items()))
            return FakeQuerySet(self.rows, key, value)

        def get(self, **kwargs):
            key, value = next(iter(kwargs.items()))
            for row in self.rows:
                if getattr(row, key) == value:
                    return row
            raise LookupError(value)

    class DomainManager:
        def __init__(self):
            self.rows = []

        def filter(self, **kwargs):
            key, value = next(iter(kwargs.items()))
            return FakeQuerySet(self.rows, key, value)

        def create(self, **kwargs):
            row = SimpleNamespace(**kwargs)
            self.rows.append(row)
            FakeDomain.created.append(row)
            return row

    tenant_manager = TenantManager()
    domain_manager = DomainManager()
    FakeTenant.objects = tenant_manager
    FakeDomain.objects = domain_manager

    monkeypatch.setattr(
        cmd_mod,
        "get_tenant_model",
        lambda: FakeTenant,
    )
    monkeypatch.setattr(
        cmd_mod,
        "get_tenant_domain_model",
        lambda: FakeDomain,
    )
    monkeypatch.setenv("PUBLIC_TENANT_EXTRA_DOMAINS", "env.example.com")
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "render.example.com")

    out = io.StringIO()
    cmd = cmd_mod.Command()
    cmd.stdout = out
    cmd.add_arguments(argparse.ArgumentParser())
    cmd.handle(domains=["cli.example.com"])

    assert "Created public tenant." in out.getvalue()
    assert "Done. Public tenant has domains" in out.getvalue()
    assert [row.domain for row in domain_manager.rows] == [
        "localhost",
        "127.0.0.1",
        "cli.example.com",
        "env.example.com",
        "render.example.com",
    ]
    assert domain_manager.rows[0].is_primary is True

    # Existing-tenant branch: leave tenant in place, pre-seed one domain.
    domain_manager.rows = [SimpleNamespace(domain="localhost")]
    out = io.StringIO()
    cmd.stdout = out
    cmd.handle(domains=[])

    assert "Public tenant already exists." in out.getvalue()
    assert any("already exists" in line for line in out.getvalue().splitlines())


def test_avatar_utils_branches(monkeypatch):
    assert get_initials("Hanan Bajramovic", None) == "HB"
    assert get_initials("Madonna", None) == "MA"
    assert get_initials("A", None) == "A"
    assert get_initials("...", None) == "U"
    assert get_initials(None, "user.name") == "UN"
    assert get_initials("", "") == "U"

    default_font = ImageFont.load_default()
    monkeypatch.setattr(
        ImageFont,
        "truetype",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no font")),
    )
    monkeypatch.setattr(ImageFont, "load_default", lambda: default_font)
    avatar = generate_initials_avatar_png("", size=64, seed="seed")
    assert avatar == b""

    monkeypatch.setattr(
        ImageFont,
        "truetype",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no font")),
    )
    monkeypatch.setattr(ImageFont, "load_default", lambda: default_font)
    avatar = generate_initials_avatar_png("AB", size=64, seed="seed")
    assert avatar.startswith(b"\x89PNG")


def test_avatar_utils_resize_branch(monkeypatch):
    original_textbbox = ImageDraw.ImageDraw.textbbox
    call_count = {"n": 0}

    def fake_textbbox(self, xy, text, font=None, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return (0, 0, 1000, 1000)
        return original_textbbox(self, xy, text, font=font, *args, **kwargs)

    monkeypatch.setattr("PIL.ImageDraw.ImageDraw.textbbox", fake_textbbox)
    avatar = generate_initials_avatar_png("AB", size=64, seed="seed")
    assert avatar.startswith(b"\x89PNG")


@pytest.mark.django_db
def test_training_budget_service_edge_cases(monkeypatch):
    emp_user = User.objects.create_user(
        username="emp", email="emp@example.com", password="x"
    )
    emp = emp_user.profile
    current_year = timezone.localdate().year

    budget = get_or_create_budget(emp, 2027, allocated_default=Decimal("123.45"))
    assert budget.allocated_budget == Decimal("123.45")
    assert (
        get_or_create_budget(emp, 2027, allocated_default=Decimal("999.99")).pk
        == budget.pk
    )

    assert recalculate_budget(emp, 2028) is None
    assert get_remaining_for_year(emp, 2028) is None

    budget = TrainingBudget.objects.create(
        employee=emp,
        fiscal_year=current_year + 2,
        allocated_budget=Decimal("0.00"),
        used_budget=Decimal("50.00"),
    )
    assert recalculate_budget(emp, current_year + 2) == budget

    budget.allocated_budget = Decimal("100.00")
    budget.used_budget = Decimal("50.00")
    budget.threshold_notified_at = timezone.now()
    budget.save(
        update_fields=["allocated_budget", "used_budget", "threshold_notified_at"]
    )
    _maybe_notify_threshold(budget)
    budget.refresh_from_db()
    assert budget.threshold_notified_at is None

    assert _resolve_hr_recipients() == []

    perm, _ = Permission.objects.get_or_create(
        module_name="Training",
        feature_action="configure_budget",
    )
    hr_role = Role.objects.create(name="HR")
    hr_role.permissions.add(perm)
    hr_user = User.objects.create_user(
        username="hr", email="hr@example.com", password="x"
    )
    hr_profile = hr_user.profile
    hr_profile.role = hr_role
    hr_profile.save(update_fields=["role"])
    assert [p.pk for p in _resolve_hr_recipients()] == [hr_profile.pk]

    notifications = []
    monkeypatch.setattr(
        "core.services.training_budget_service.create_notification",
        lambda **kwargs: notifications.append(kwargs) or True,
    )
    # alert_budget = TrainingBudget.objects.create(
    #     employee=emp,
    #     fiscal_year=current_year,
    #     allocated_budget=Decimal("100.00"),
    #     used_budget=Decimal("0.00"),
    # )
    TrainingEntry.objects.create(
        employee=emp,
        course_title="Course",
        provider="Provider",
        training_date=date(current_year, 1, 15),
        cost=Decimal("120.00"),
    )
    recalculate_budget(emp, current_year)
    assert notifications
    assert notifications[0]["recipient"] == emp
