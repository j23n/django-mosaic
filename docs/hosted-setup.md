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

## The dashboard (`/dashboard`)

Signed-in tenants arrange their home at `/dashboard` on the base domain:

- **Sections** — show/hide, retitle, and reorder the per-collection sections
  of their home page. Collections they publish to later appear automatically
  until configured.
- **Theme** — a preset (`plain`, `paper`, `night`) plus individual design
  tokens: accent/background/text colors, a font choice (sans/serif/mono),
  and corner radius. Tokens become `--mosaic-*` CSS custom properties on the
  tenant page; every value is validated against a fixed vocabulary (hex
  colors, enums) on both write **and** read, so a hand-edited record cannot
  inject CSS. Free-form custom CSS is a later, deliberate tier — see the
  plan's customization ladder.

The entire configuration is saved as a single `blog.mosaic.site.settings`
record (rkey `self`) **in the tenant's own PDS**, written through their
OAuth grant. Nothing about how their site looks lives in the service
database — pointing any mosaic instance at the handle reproduces the site,
configuration included. Reads are public XRPC, cached ~5 minutes.

## Custom domains

Tenants connect their own domain from the dashboard. There is no explicit
verification step — control is proven by serving:

1. The tenant enters `blog.example.com` on `/dashboard` (validated: real
   hostname, not under the base domain, not registered to another tenant).
2. They point DNS at your server: a CNAME to `DOMAIN_TARGET` (defaults to
   the base domain), or ALIAS/ANAME at an apex.
3. TLS is issued on demand. Configure your proxy to ask us first — with
   Caddy:

   ```
   {
       on_demand_tls {
           ask http://127.0.0.1:8000/domains/check
       }
   }

   https:// {
       tls {
           on_demand
       }
       reverse_proxy 127.0.0.1:8000
   }
   ```

   `/domains/check?domain=<host>` answers 200 only for domains an active
   tenant has registered, so certificates are never requested for hosts we
   won't serve. Issuance itself can only succeed if the DNS actually points
   at us (ACME), which is the real ownership proof.
4. The first request arriving with that Host marks the domain verified
   (`domain_verified_at`); the dashboard flips from "waiting for DNS" to
   connected.

A squatted domain string (registered here but never pointed at us) never
verifies and can be cleared in the admin.

### Domain-as-handle

Tenant hosts serve `/.well-known/atproto-did` with the tenant's DID, so a
connected custom domain can become the tenant's ATProto *handle*: in
Bluesky, Settings → Handle → "I have my own domain" → "No DNS panel". The
dashboard shows this wizard once the domain is connected. This is the
"your domain is your identity" move nobody else productizes.

## Writing (`/dashboard/write`)

Tenants publish directly from the dashboard: title, optional description,
markdown body (≤30 kB for now). The composer writes a
`site.standard.document` record into *their* repo through their OAuth
grant — a `site.standard.publication` record (rkey `self`) is created on
first publish so other standard.site readers can attribute the document.
The rkey is a TID minted client-side, so the record's `path` is its
permalink from the start.

Published documents get pages on the tenant site at `/posts/<rkey>` —
markdown renders from the document's mosaic content block
(bleach-sanitized), falling back to `textContent` for documents written by
other apps. The "Writing" section on the home page links to these pages.

## Custom CSS

The last rung of the customization ladder before self-hosting: a
stylesheet textarea on the dashboard, stored in the same settings record
(`customCss`, capped at 20 k characters) and served at `/custom.css` on the
tenant's own host — as a standalone `text/css` response (never inlined),
so it can style but not inject markup. Theme tokens remain available to it
as `--mosaic-*` custom properties.

## Reports and moderation

`/report?site=<subdomain or custom domain>` on the base domain files an
abuse report (anonymous, optional contact, honeypot-filtered, per-IP
throttled). Reports appear in the admin with resolve and
suspend-the-reported-tenant actions; tenant pages link the form in their
footer. Suspension takes effect on the next request — the middleware
checks status on every hit — and also drops the domain from the on-demand
TLS ask endpoint. Pair this with actual ToS/legal pages for your
deployment.

## Data freshness

Tenant pages read from TTL caches (records ~5 min, profiles ~10 min,
settings ~5 min). For a hosted service, run the Jetstream consumer as a
long-lived process next to the web workers:

```
pip install django-mosaic[jetstream]
python manage.py atproto jetstream
```

It opens one websocket with `wantedDids` = the owner plus every active
tenant and drops the relevant caches the moment an account writes to its
repo — publish something anywhere in the ATmosphere and your mosaic home
updates seconds later. It resumes from a stored cursor after restarts and
is purely an optimization: if it's down, pages just fall back to TTL
staleness. (Jetstream caps `wantedDids` at 10 000 — shard consumers when
you outgrow that.)

## Not yet built (see the plan)

Billing/paid tiers (Stripe), moderation-label handling, media uploads and
edit/delete in the composer.
