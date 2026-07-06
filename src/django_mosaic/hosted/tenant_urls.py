"""URLconf swapped in by TenantMiddleware for tenant subdomains.

Kept deliberately minimal: the tenant's whole site is their PDS content.
Per-collection pages and dashboard routes land here in later milestones.
"""

from django.urls import path

from .views import tenant_home

urlpatterns = [
    path("", tenant_home, name="tenant-home"),
]
