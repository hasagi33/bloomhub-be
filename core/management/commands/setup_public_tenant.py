from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """
    Compatibility wrapper for tests/CI environments.

    In non-tenant setups (e.g. SQLite test DB), the `tenants` Django app may not
    be installed, which would make the `setup_public_tenant` command unknown.
    This wrapper keeps the command name available and no-ops when multi-tenancy
    is disabled.
    """

    help = "Set up the public tenant (no-op when tenants are disabled)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            action="append",
            dest="domains",
            default=[],
            metavar="HOSTNAME",
        )

    def handle(self, *args, **options):
        # When tenants are disabled, don't attempt schema/domain creation.
        if not getattr(settings, "USE_TENANTS", False):
            self.stdout.write("Tenants disabled; skipping setup_public_tenant.")
            return

        # Delegate to the real command from the `tenants` app.
        from tenants.management.commands.setup_public_tenant import (
            Command as TenantsSetupCommand,
        )

        TenantsSetupCommand().handle(*args, **options)
