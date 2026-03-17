"""
Create the public tenant and domains for localhost and 127.0.0.1.

Run once after migrate_schemas --shared when using PostgreSQL + django-tenants:

    python manage.py setup_public_tenant

Safe to run multiple times; skips creation if public tenant already exists.
"""

from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_domain_model, get_tenant_model

PUBLIC_SCHEMA_NAME = "public"
LOCALHOST_DOMAINS = ("localhost", "127.0.0.1")


class Command(BaseCommand):
    help = (
        "Create public tenant with localhost and 127.0.0.1 domains so local dev works."
    )

    def handle(self, *args, **options):
        Tenant = get_tenant_model()
        Domain = get_tenant_domain_model()

        if Tenant.objects.filter(schema_name=PUBLIC_SCHEMA_NAME).exists():
            self.stdout.write("Public tenant already exists.")
            for domain in LOCALHOST_DOMAINS:
                if Domain.objects.filter(domain=domain).exists():
                    self.stdout.write(f"  Domain '{domain}' already exists.")
                else:
                    tenant = Tenant.objects.get(schema_name=PUBLIC_SCHEMA_NAME)
                    Domain.objects.create(
                        domain=domain,
                        tenant=tenant,
                        is_primary=(domain == LOCALHOST_DOMAINS[0]),
                    )
                    self.stdout.write(self.style.SUCCESS(f"  Added domain '{domain}'."))
            return

        tenant = Tenant(
            schema_name=PUBLIC_SCHEMA_NAME,
            name="Public (localhost)",
        )
        tenant.save()
        self.stdout.write(self.style.SUCCESS("Created public tenant."))

        for i, domain in enumerate(LOCALHOST_DOMAINS):
            Domain.objects.create(
                domain=domain,
                tenant=tenant,
                is_primary=(i == 0),
            )
            self.stdout.write(self.style.SUCCESS(f"  Added domain '{domain}'."))

        self.stdout.write(
            self.style.SUCCESS(
                "Done. You can use http://localhost:8000 and http://127.0.0.1:8000"
            )
        )
