from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command


@pytest.fixture(scope="session", autouse=True)
def seed_permissions(django_db_setup, django_db_blocker):
    repo_root = Path(__file__).resolve().parent
    permissions_csv = repo_root / "permissions.csv"

    with django_db_blocker.unblock():
        call_command("load_permissions", str(permissions_csv), verbosity=0)
