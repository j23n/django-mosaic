# Multi-tenant hosting (`django_mosaic.hosted`)

Opt-in app for running one mosaic instance that serves many personal sites —
the engine behind the hosted product plan (`docs/hosted-plan.md`). A tenant
is an ATProto account bound to a subdomain; their site is rendered live from
their PDS, so the registry row plus their handle reproduces the whole site.

Self-hosted single-site installs do not need this app.

## Setup

```python
INSTALLED_APPS += ["django_mosaic.hosted"]

MIDDLEWARE += ["django_mosaic.hosted.middleware.TenantMiddleware"]

ALLOWED_HOSTS = ["mosaic.example", ".mosaic.example"]  # note the leading dot

MOSAIC_HOSTED = {
    "BASE_DOMAIN": "mosaic.example",
    # "RESERVED_SUBDOMAINS": ["extra", "names"],  # merged with built-ins
    # "CLAIM_OPEN": True,  # set False to freeze signups, keep sites served
}
```

Include the claim route on the base domain, next to the atproto URLs (which
provide `/oauth/*` — the claim flow requires the [OAuth
client](atproto-setup.md#sign-in-with-atproto-oauth) to be configured):

```python
urlpatterns = [
    path("", include("django_mosaic.atproto.urls")),
    path("", include("django_mosaic.hosted.urls")),
    # ...
]
```

Run `python manage.py migrate`. DNS needs a wildcard record
(`*.mosaic.example`) and a matching wildcard TLS certificate.

## How requests are routed

- `mosaic.example` (or any host not under the base domain) — untouched;
  serves your normal URLconf: the preview landing page (`PREVIEW_LANDING`),
  `/claim`, `/oauth/*`, the admin, etc.
- `alice.mosaic.example` — `TenantMiddleware` looks up the active tenant
  and swaps in `django_mosaic.hosted.tenant_urls`, whose root renders the
  tenant's home from their PDS (profile header + a section per known
  collection). Unknown or suspended subdomains 404; nested subdomains are
  rejected.

The tenant page template is `hosted/home.html` — override it in your
deployment to restyle all tenant sites at once. If a tenant's PDS cannot be
resolved the page degrades to a 503 (`hosted/unavailable.html`).

## Claiming

Visitors sign in at `/oauth/login` with their own ATProto account, then pick
a subdomain at `/claim`. Ownership is proven by the OAuth grant — a visitor
can only claim a site for the DID they are signed in as, and each DID can
hold one subdomain. Slugs are validated against DNS-label rules and a
reserved list (`www`, `api`, `oauth`, ... plus your `RESERVED_SUBDOMAINS`).

Moderation: the `Tenant` admin has suspend/reactivate actions; a suspended
tenant's subdomain stops serving immediately.

## Not yet built (see the plan)

Dashboard (sections/theme/tokens), settings as PDS records, custom domains,
billing, Jetstream-driven cache invalidation.
