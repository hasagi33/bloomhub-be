"""
Create the public tenant and domains for localhost, 127.0.0.1, and deployment hostnames.

Run once after migrate_schemas --shared when using PostgreSQL + django-tenants:

    python manage.py setup_public_tenant
    python manage.py setup_public_tenant --domain bloomhub-be.onrender.com

Or set PUBLIC_TENANT_EXTRA_DOMAINS=bloomhub-be.onrender.com (comma-separated) in the
deployment environment so deploy can add the hostname automatically.

Safe to run multiple times; skips creation if public tenant already exists.
"""

import os

from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_domain_model, get_tenant_model

PUBLIC_SCHEMA_NAME = "public"
LOCALHOST_DOMAINS = ("localhost", "127.0.0.1")


class Command(BaseCommand):
    help = (
        "Create public tenant with localhost/127.0.0.1 and optional deployment domains."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--domain",
            action="append",
            dest="domains",
            default=[],
            metavar="HOSTNAME",
            help="Add this hostname to the public tenant (e.g. bloomhub-be.onrender.com). "
            "Can be repeated.",
        )

    def handle(self, *args, **options):
        Tenant = get_tenant_model()
        Domain = get_tenant_domain_model()

        extra = list(options["domains"] or [])
        env_domains = os.environ.get("PUBLIC_TENANT_EXTRA_DOMAINS", "").strip()
        if env_domains:
            extra.extend(d.strip() for d in env_domains.split(",") if d.strip())
        # Render sets RENDER_EXTERNAL_HOSTNAME (e.g. bloomhub-be.onrender.com)
        render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
        if render_host and render_host not in extra:
            extra.append(render_host)
        all_domains = list(LOCALHOST_DOMAINS) + extra

        if Tenant.objects.filter(schema_name=PUBLIC_SCHEMA_NAME).exists():
            self.stdout.write("Public tenant already exists.")
            tenant = Tenant.objects.get(schema_name=PUBLIC_SCHEMA_NAME)
            for domain in all_domains:
                if Domain.objects.filter(domain=domain).exists():
                    self.stdout.write(f"  Domain '{domain}' already exists.")
                else:
                    Domain.objects.create(
                        domain=domain,
                        tenant=tenant,
                        is_primary=False,
                    )
                    self.stdout.write(self.style.SUCCESS(f"  Added domain '{domain}'."))
            return

        tenant = Tenant(
            schema_name=PUBLIC_SCHEMA_NAME,
            name="Public",
        )
        tenant.save()
        self.stdout.write(self.style.SUCCESS("Created public tenant."))

        for i, domain in enumerate(all_domains):
            Domain.objects.create(
                domain=domain,
                tenant=tenant,
                is_primary=(i == 0),
            )
            self.stdout.write(self.style.SUCCESS(f"  Added domain '{domain}'."))

        self.stdout.write(
            self.style.SUCCESS(
                "Done. Public tenant has domains: " + ", ".join(all_domains)
            )
        )
