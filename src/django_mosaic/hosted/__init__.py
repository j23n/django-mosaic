"""Multi-tenant hosting for mosaic (the SaaS seam).

Opt-in sub-app for running one mosaic instance that serves many personal
sites: a tenant registry (DID ↔ subdomain), Host-header routing middleware,
and a claim flow gated on ATProto OAuth sign-in. Self-hosted single-site
installs never need this app.

Setup::

    INSTALLED_APPS += ["django_mosaic.hosted"]
    MIDDLEWARE += ["django_mosaic.hosted.middleware.TenantMiddleware"]
    MOSAIC_HOSTED = {"BASE_DOMAIN": "mosaic.example"}

Requests to ``<subdomain>.<BASE_DOMAIN>`` are routed to that tenant's site
(rendered straight from their PDS via the atproto preview engine); requests
to ``BASE_DOMAIN`` itself fall through to the normal URLconf (landing page,
claim flow, admin...).
"""
