from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Client(TenantMixin):
    """Tenant (schema) for multi-tenant routing. Public schema serves the main app."""

    name = models.CharField(max_length=100)
    created_on = models.DateField(auto_now_add=True)

    auto_create_schema = True
    auto_drop_schema = False


class Domain(DomainMixin):
    """Hostname that maps to a tenant. Use 'localhost' and '127.0.0.1' for local dev."""

    pass
