"""Host-header tenant routing.

For requests to ``<subdomain>.<BASE_DOMAIN>``, resolves the tenant and swaps
in the tenant URLconf (``request.urlconf``), so the whole site at that host
is the tenant's home. Requests to the base domain itself — or to any host
not under it, e.g. the admin on an internal name — pass through untouched.

Add after Django's common middleware::

    MIDDLEWARE += ["django_mosaic.hosted.middleware.TenantMiddleware"]

Note ``ALLOWED_HOSTS`` must include ``.mosaic.example`` (leading dot) so
Django accepts the subdomain hosts in the first place.
"""

import logging

from django.http import Http404
from django.utils import timezone

from . import conf
from .models import Tenant

logger = logging.getLogger("django_mosaic.hosted")


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        tenant = self._custom_domain_tenant(request)
        if tenant is None:
            subdomain = self._subdomain(request)
            if subdomain:
                tenant = Tenant.objects.filter(subdomain=subdomain).first()
                if tenant is None:
                    raise Http404(f"No site at {subdomain}.{conf.base_domain()}.")
        if tenant is not None:
            if tenant.status != Tenant.STATUS_ACTIVE:
                raise Http404("This site is unavailable.")
            request.tenant = tenant
            request.urlconf = "django_mosaic.hosted.tenant_urls"
        return self.get_response(request)

    @staticmethod
    def _custom_domain_tenant(request):
        """The tenant whose custom domain is this request's Host, or None.

        A request arriving here proves the domain resolves to us over a cert
        we issued (via the on-demand TLS ask endpoint), so the first hit
        counts as domain verification.
        """
        if not conf.enabled():
            return None
        host = request.get_host().split(":", 1)[0].lower().strip(".")
        base = conf.base_domain()
        if host == base or host.endswith("." + base):
            return None
        tenant = Tenant.objects.filter(custom_domain=host).first()
        if tenant is not None and tenant.domain_verified_at is None:
            tenant.domain_verified_at = timezone.now()
            tenant.save(update_fields=["domain_verified_at"])
            logger.info("Custom domain verified: %s -> %s", host, tenant.did)
        return tenant

    @staticmethod
    def _subdomain(request):
        """The tenant subdomain for this request, or None to pass through."""
        if not conf.enabled():
            return None
        host = request.get_host().split(":", 1)[0].lower().strip(".")
        base = conf.base_domain()
        if host == base or not host.endswith("." + base):
            return None
        label = host[: -len(base) - 1]
        if "." in label:  # nested subdomains are not tenant hosts
            raise Http404("Unknown host.")
        return label
