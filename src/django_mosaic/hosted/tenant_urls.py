"""URLconf swapped in by TenantMiddleware for tenant subdomains.

Kept deliberately minimal: the tenant's whole site is their PDS content.
Per-collection pages and dashboard routes land here in later milestones.
"""

from django.urls import path

from .views import tenant_home, tenant_wellknown_did

urlpatterns = [
    path("", tenant_home, name="tenant-home"),
    # Domain-as-handle: answers the ATProto handle-verification fetch with
    # the tenant's DID (meaningful on verified custom domains).
    path(".well-known/atproto-did", tenant_wellknown_did, name="tenant-atproto-did"),
]
