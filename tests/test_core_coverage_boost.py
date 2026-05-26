import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from core.avatar_utils import get_initials
from core.enums import DocumentSignatureStatus, TrackedField
from core.models import (
    Document,
    EmployeeProfileChangeHistory,
    Project,
    ProjectAssignment,
    Role,
    UserProfile,
)
from core.services import document_query_service, profile_change_history
from core.services.mail import recipients


@pytest.mark.django_db
def test_recipient_helpers_and_hr_lookup(monkeypatch):
    user = SimpleNamespace(
        email=" user@example.com ",
        get_full_name=lambda: "  ",
        username="uname",
    )
    profile = SimpleNamespace(
        email_address=" profile@example.com ",
        full_name="  Profile Name  ",
        user=user,
    )
    blank_profile = SimpleNamespace(
        email_address=None,
        full_name=None,
        user=SimpleNamespace(email="", get_full_name=lambda: "", username="fallback"),
    )

    assert recipients.profile_email(profile) == "profile@example.com"
    assert recipients.profile_email(blank_profile) == ""
    assert recipients.display_name(profile) == "Profile Name"
    assert recipients.display_name(blank_profile) == "fallback"
    assert recipients.first_name(profile) == "Profile"
    assert recipients.first_name(blank_profile) == "fallback"
    assert get_initials("Hanan Bajramovic", None) == "HB"
    assert get_initials("Madonna", None) == "MA"
    assert get_initials(None, "user.name") == "UN"
    assert get_initials("", "") == "U"

    hr_role = Role.objects.create(name="HR")
    hr_user_one = User.objects.create_user(
        username="hr1",
        email="hr1@example.com",
        password="x",
    )
    hr_profile_one = hr_user_one.profile
    hr_profile_one.role = hr_role
    hr_profile_one.email_address = " hr1@example.com "
    hr_profile_one.save(update_fields=["role", "email_address"])

    hr_user_two = User.objects.create_user(
        username="hr2",
        email="hr2@example.com",
        password="x",
    )
    hr_profile_two = hr_user_two.profile
    hr_profile_two.role = hr_role
    hr_profile_two.email_address = None
    hr_profile_two.save(update_fields=["role", "email_address"])

    assert set(recipients.hr_recipient_emails()) == {
        "hr1@example.com",
        "hr2@example.com",
    }

    hr_profile_one.employment_status = UserProfile.EmploymentStatus.INACTIVE
    hr_profile_one.save(update_fields=["employment_status", "is_active"])
    hr_profile_two.employment_status = UserProfile.EmploymentStatus.INACTIVE
    hr_profile_two.save(update_fields=["employment_status", "is_active"])

    warnings = []
    monkeypatch.setattr(recipients.logger, "warning", lambda msg: warnings.append(msg))
    assert recipients.hr_recipient_emails() == []
    assert warnings

    staff = User.objects.create_user(
        username="staff",
        email="staff@example.com",
        password="x",
    )
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    assert recipients.hr_recipient_emails() == ["staff@example.com"]


@pytest.mark.django_db
def test_profile_change_history_helpers():
    employee_user = User.objects.create_user(
        username="employee",
        email="employee@example.com",
        first_name="Employee",
        last_name="One",
        password="x",
    )
    changed_by = User.objects.create_user(
        username="actor",
        email="actor@example.com",
        password="x",
    )
    manager_user = User.objects.create_user(
        username="manager",
        email="manager@example.com",
        first_name="Manager",
        last_name="Person",
        password="x",
    )

    employee_profile = employee_user.profile
    manager_profile = manager_user.profile
    manager_profile.full_name = ""
    manager_profile.save(update_fields=["full_name"])

    assert (
        profile_change_history.log_employee_profile_change(
            employee=employee_profile,
            field=TrackedField.ROLE,
            old_value="a",
            new_value="a",
            changed_by=changed_by,
        )
        is None
    )

    history = profile_change_history.log_employee_profile_change(
        employee=employee_profile,
        field=TrackedField.ROLE,
        old_value={"id": 1},
        new_value={"id": 2},
        changed_by=changed_by,
        metadata={"source": "test"},
    )
    assert history is not None
    assert EmployeeProfileChangeHistory.objects.count() == 1
    assert history.changed_by_id == changed_by.id
    assert history.metadata == {"source": "test"}

    role = Role.objects.create(name="Reviewer")
    assert profile_change_history.role_value(None) is None
    assert profile_change_history.role_value(role) == {
        "id": role.id,
        "name": role.name,
    }
    assert (
        profile_change_history._as_manager_user_id(manager_profile)
        == manager_profile.user_id
    )
    assert profile_change_history._as_manager_user_id("9") == 9
    assert profile_change_history._as_manager_user_id(0) is None
    assert profile_change_history._as_manager_user_id("bad") is None

    payload = profile_change_history.manager_payload_from_ids(
        [manager_profile.user_id, 999999]
    )
    assert payload == {
        "ids": [manager_profile.user_id, 999999],
        "names": ["Manager Person", "999999"],
    }


@pytest.mark.django_db
def test_document_query_service_filters_and_lookup():
    uploader = User.objects.create_user(username="uploader", password="x")
    employee = User.objects.create_user(username="employee", password="x")
    today = timezone.localdate()

    active_doc = Document.objects.create(
        employee=employee.profile,
        uploaded_by=uploader.profile,
        category=Document.Category.CONTRACTS,
        file_key="docs/active.pdf",
        name="Active Contract",
        description="Needle in the haystack",
        tags=["needle", "searchable"],
        signature_status=DocumentSignatureStatus.PENDING,
        expiry_date=today + timedelta(days=5),
        archived=False,
    )
    archived_doc = Document.objects.create(
        employee=employee.profile,
        uploaded_by=uploader.profile,
        category=Document.Category.POLICIES,
        file_key="docs/archive.pdf",
        name="Archived Policy",
        description="Archived item",
        tags=["old"],
        signature_status=DocumentSignatureStatus.SIGNED,
        expiry_date=today - timedelta(days=2),
        archived=True,
    )

    assert document_query_service.document_queryset().count() == 1
    assert document_query_service.document_queryset(include_archived=True).count() == 2
    assert (
        document_query_service.get_document_for_api(active_doc.pk).pk == active_doc.pk
    )
    assert document_query_service.get_document_for_api(999999) is None
    assert (
        document_query_service.get_document_for_response(active_doc.pk).pk
        == active_doc.pk
    )
    with pytest.raises(Document.DoesNotExist):
        document_query_service.get_document_for_response(999999)

    filtered = document_query_service.apply_document_list_filters(
        Document.objects.all(),
        {
            "category": Document.Category.CONTRACTS,
            "signature_status": DocumentSignatureStatus.PENDING,
            "expiry_filter": "expiring_soon",
            "search": "needle",
            "ordering": "name",
        },
    )
    assert list(filtered.values_list("pk", flat=True)) == [active_doc.pk]

    expired = document_query_service.apply_document_list_filters(
        Document.objects.all(),
        {"expiry_filter": "expired"},
    )
    assert list(expired.values_list("pk", flat=True)) == [archived_doc.pk]

    with pytest.raises(document_query_service.DocumentFilterError):
        document_query_service.apply_document_list_filters(
            Document.objects.all(),
            {"category": "invalid"},
        )
    with pytest.raises(document_query_service.DocumentFilterError):
        document_query_service.apply_document_list_filters(
            Document.objects.all(),
            {"signature_status": "invalid"},
        )
    with pytest.raises(document_query_service.DocumentFilterError):
        document_query_service.apply_document_list_filters(
            Document.objects.all(),
            {"expiry_filter": "invalid"},
        )


def test_generate_avatar_command_branches(monkeypatch):
    from core.management.commands import generate_avatar as cmd_mod

    out = io.StringIO()
    call_command("generate_avatar", stdout=out)
    assert "Please provide either --username or --email" in out.getvalue()

    monkeypatch.setattr(
        cmd_mod.User.objects,
        "get",
        lambda **kwargs: (_ for _ in ()).throw(cmd_mod.User.DoesNotExist()),
    )
    out = io.StringIO()
    call_command("generate_avatar", username="missing", stdout=out)
    assert "User not found" in out.getvalue()

    fake_user_missing_profile = SimpleNamespace(
        id=7,
        username="noprof",
        email="noprof@example.com",
        get_full_name=lambda: "No Profile",
    )
    monkeypatch.setattr(
        cmd_mod.User.objects, "get", lambda **kwargs: fake_user_missing_profile
    )
    out = io.StringIO()
    call_command("generate_avatar", username="noprof", stdout=out)
    assert "User profile not found" in out.getvalue()

    class FakeAvatar:
        def __init__(self):
            self.saved = None

        def save(self, name, content, save=True):
            self.saved = (name, content.read(), save)

    fake_avatar = FakeAvatar()
    fake_user = SimpleNamespace(
        id=8,
        username="avatar-user",
        email="avatar@example.com",
        get_full_name=lambda: "Avatar User",
        profile=SimpleNamespace(full_name="Avatar User", avatar=fake_avatar),
    )
    monkeypatch.setattr(cmd_mod.User.objects, "get", lambda **kwargs: fake_user)
    monkeypatch.setattr(cmd_mod, "get_initials", lambda name, username: "AU")
    monkeypatch.setattr(
        cmd_mod,
        "generate_initials_avatar_png",
        lambda initials, seed=None: b"png-bytes",
    )

    out = io.StringIO()
    call_command("generate_avatar", username="avatar-user", stdout=out)
    assert "Avatar generated successfully" in out.getvalue()
    assert fake_avatar.saved == ("avatar.png", b"png-bytes", True)


def test_regenerate_avatars_command_branches(monkeypatch):
    from core.management.commands import regenerate_avatars as cmd_mod

    class FakeAvatar:
        def __init__(self, name=""):
            self.name = name
            self.saved = None
            self.deleted = False

        def __bool__(self):
            return bool(self.name)

        def delete(self, save=False):
            self.deleted = True

        def save(self, name, content, save=True):
            self.name = name
            self.saved = (name, content.read(), save)

    class FakeProfile:
        def __init__(self, pk, user_id, username, full_name, avatar):
            self.id = pk
            self.pk = pk
            self.user_id = user_id
            self.user = SimpleNamespace(username=username)
            self.full_name = full_name
            self.avatar = avatar

    class FakeQuerySet:
        def __init__(self, profiles):
            self.profiles = list(profiles)

        def order_by(self, *args, **kwargs):
            return self

        def filter(self, **kwargs):
            filtered = self.profiles
            if "id" in kwargs:
                filtered = [p for p in filtered if p.id == kwargs["id"]]
            if "user_id" in kwargs:
                filtered = [p for p in filtered if p.user_id == kwargs["user_id"]]
            if "avatar__isnull" in kwargs:
                want_null = kwargs["avatar__isnull"]
                filtered = [p for p in filtered if bool(p.avatar) is not want_null]
            return FakeQuerySet(filtered)

        def count(self):
            return len(self.profiles)

        def iterator(self):
            return iter(self.profiles)

    missing_avatar = FakeAvatar()
    existing_avatar = FakeAvatar("avatars/old.png")
    missing_profile = FakeProfile(1, 11, "missing", "Missing Avatar", missing_avatar)
    existing_profile = FakeProfile(
        2, 22, "existing", "Existing Avatar", existing_avatar
    )

    monkeypatch.setattr(
        UserProfile.objects,
        "all",
        lambda: FakeQuerySet([missing_profile, existing_profile]),
    )
    monkeypatch.setattr(cmd_mod, "get_initials", lambda full_name, username: "AV")
    monkeypatch.setattr(
        cmd_mod,
        "generate_initials_avatar_png",
        lambda initials, seed=None: b"avatar-bytes",
    )

    out = io.StringIO()
    call_command("regenerate_avatars", stdout=out)
    assert "Regenerating 1 profile avatar(s)..." in out.getvalue()
    assert missing_avatar.saved == ("avatar.png", b"avatar-bytes", True)
    assert existing_avatar.saved is None
    assert existing_avatar.deleted is False

    monkeypatch.setattr(cmd_mod, "uuid4", lambda: SimpleNamespace(hex="seed"))
    out = io.StringIO()
    call_command(
        "regenerate_avatars",
        all=True,
        profile_id=existing_profile.id,
        random=True,
        stdout=out,
    )
    assert "Regenerating 1 profile avatar(s)..." in out.getvalue()
    assert existing_avatar.deleted is True
    assert existing_avatar.saved == ("avatar.png", b"avatar-bytes", True)


@pytest.mark.django_db
def test_setup_super_admin_updates_existing_user_without_permissions():
    user = User.objects.create_user(
        username="admin-user",
        email="admin@example.com",
        password="x",
    )
    user.is_staff = False
    user.is_superuser = False
    user.save(update_fields=["is_staff", "is_superuser"])

    out = io.StringIO()
    call_command(
        "setup_super_admin",
        username="admin-user",
        stdout=out,
    )

    user.refresh_from_db()
    assert user.is_staff is True
    assert user.is_superuser is True
    assert "Assigned SUPER_ADMIN" in out.getvalue()


@pytest.mark.django_db
def test_seed_project_and_vacation_data_commands():
    out = io.StringIO()
    call_command("seed_projects_data_model", stdout=out)
    assert "Seeded projects + assignments" in out.getvalue()
    assert (
        Project.objects.filter(name__in=["Acme Portal", "Internal Tooling"]).count()
        == 2
    )
    assert ProjectAssignment.objects.filter(project__name="Acme Portal").exists()

    out = io.StringIO()
    call_command(
        "seed_vacations_test_data",
        include_superuser=False,
        stdout=out,
    )
    assert "Vacations test data seeded." in out.getvalue()
    assert (
        Project.objects.filter(name__in=["Project Alpha", "Project Beta"]).count() == 2
    )
    assert (
        User.objects.filter(username__in=["alice", "bob", "carol", "dave"]).count() == 4
    )
