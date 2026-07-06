# Architecture

This document describes how mosaic is put together: the layers, the data
model, how a request is served, how caching and settings work, and the
security model. It's the map you want before changing anything non-trivial.

For *setup* instructions see the [README](../README.md),
[`atproto-setup.md`](atproto-setup.md), and [`hosted-setup.md`](hosted-setup.md).
For the *why* behind the ATProto direction see
[`atproto-design.md`](atproto-design.md) and [`hosted-plan.md`](hosted-plan.md).

## What mosaic is

Mosaic is a reusable Django app (installed as a package into other projects,
not a standalone service). It started as an IndieWeb blog engine and grew into
**a personal AppView of your [AT Protocol](https://atproto.com) repository** —
your posts, photos, repos, books, and other ATmosphere content rendered as one
site you control.

It runs in three shapes, each a superset of the one before. You opt into each
by adding an app to `INSTALLED_APPS`; nothing turns on until you configure it.

| Shape | Apps enabled | What you get |
|---|---|---|
| **Blog** | `django_mosaic` | Self-hosted markdown blog: admin editor, public/private namespaces, RSS, sitemaps. No network dependencies. |
| **Aggregator** | `+ django_mosaic.atproto` | Publish posts to your PDS, render your other lexicon collections as pages, show ATmosphere reactions, preview any handle, sign in with ATProto. |
| **Hosted** | `+ django_mosaic.hosted` | Multi-tenant SaaS: one deployment serves many people's sites, each claimed and configured from their own PDS. |

## Layers and dependency direction

```
┌─────────────────────────────────────────────────────────────┐
│ django_mosaic.hosted     multi-tenant: Tenant registry,      │
│                          Host routing, claim, dashboard,     │
│                          composer, custom domains, reports   │
│         │ depends on                                         │
│         ▼                                                    │
│ django_mosaic.atproto    the PDS bridge: identity, publish,  │
│                          lexicon/preview reads, reactions,   │
│                          OAuth client, Jetstream consumer    │
│         │ depends on                                         │
│         ▼                                                    │
│ django_mosaic            the blog engine: Post/Namespace/    │
│                          Tag/Author, admin editor, feeds     │
└─────────────────────────────────────────────────────────────┘
```

Dependencies point **downward only**. The core engine has no idea ATProto
exists; the bridge has no idea multi-tenancy exists. This is what lets each
shape ship independently and keeps a plain blog install free of ATProto
dependencies. The one hook the other direction is a `post_save` signal on
`Post` (in `atproto/signals.py`) that fires an optional publish — it degrades
to a no-op when the bridge is unconfigured and never breaks a save.

The `atproto` and `hosted` layers keep their optional third-party dependencies
behind **extras**, imported lazily so the package installs and the apps import
without them:

- `oauth` → `pyjwt`, `cryptography` (OAuth client, `atproto/oauth/`)
- `jetstream` → `websockets` (the live-invalidation daemon)
- `deploy` → `fabric` (VPS deployment tooling)

## The core engine (`django_mosaic`)

A conventional Django blog, deliberately small.

### Data model (`models.py`)

- **`Post`** — the central model. Markdown `content`, a generated `slug`, a
  `summary` derived from the rendered HTML, `published_at`, an `is_published`
  flag, tags, and a `published_version` FK into `reversion.Version` (so a
  published post pins a specific revision while drafts move ahead).
- **`Namespace`** — posts live in a namespace. Two ship by default: `public`
  (visible to everyone) and `private` (visible only to holders of a post's
  `secret_id`, enforced by `MagicAuthorizationMiddleware`). Drafts and the
  private namespace **never** sync to ATProto.
- **`Tag`** — unique per namespace.
- **`ContentImage`** — uploaded images attached to a post, processed on save
  (RGB/JPEG conversion, thumbnailing); files are cleaned up on delete.
- **`Author`**, **`RelMeLink`** — site identity and IndieWeb `rel="me"` links.

### Editing and rendering

- The admin uses the **martor** markdown editor with a drag-and-drop
  multi-image upload zone; uploads create processed `ContentImage` rows
  immediately (`atproto`-independent; lives in core `uploads.py`).
- Markdown is rendered and sanitized through **django-markdownify** (bleach),
  configured on in the shipped settings template.
- Views (`views.py`) resolve a namespace, paginate (`MOSAIC_PAGE_SIZE`), and
  render list/detail/tag/feed pages. `feeds.py` and `sitemaps.py` provide RSS
  and sitemaps.

### Scaffolding and deployment

- `mosaic-admin init <name>` (`scaffold.py`, a `[project.scripts]` console
  script) writes a complete runnable Django project.
- `manage.py mosaic deployment ...` (`management/commands/_deployment.py`,
  needs the `deploy` extra) automates a Docker + nginx + certbot VPS deploy.

## The ATProto bridge (`django_mosaic.atproto`)

App label `django_mosaic_atproto`. Turns mosaic into a personal AppView. All
configuration lives under one `MOSAIC_ATPROTO` settings dict, merged over
`conf.DEFAULTS`; the bridge is inert until `HANDLE` + `APP_PASSWORD` are set.

### Identity (`identity.py`, `client.py`)

- **`Identity(handle, did, pds_url)`** is the value every read path operates
  on. `resolve(handle)` resolves a handle → DID → PDS via public directories
  (`resolveHandle`, `plc.directory`, `did:web`), cached per handle.
  `resolve_did(did)` resolves the **immutable DID** directly — used wherever
  the DID is the trusted identifier (hosted tenants) so a later handle
  takeover can't redirect a render.
- `client.py` is a tiny XRPC layer: `resolve_identity`, `resolve_pds`, an
  app-password `Session` (record create/put/delete, blob upload), and
  unauthenticated `xrpc_get`. Every PDS/endpoint URL discovered from an
  attacker-controllable document is **SSRF-validated** (`_validate_pds_url`:
  https only, no IP literals, no internal hosts).

### Writing to the PDS (`publisher.py`, `signals.py`)

On publish (auto via `post_save` signal, or `manage.py atproto publish`), a
public post becomes a `site.standard.document` record in your repo — with a
`coverImage` blob, a mosaic-native markdown `content` block, and a companion
`app.bsky.feed.post`. `models.py` tracks the mapping (`PublicationRecord`,
`DocumentRecord`).

### Reading other collections (`lexicons.py`, `preview.py`, `views.py`)

The "AppView" half. Any collection in a repo renders straight from JSON — no
local models, no migrations — through a **template-per-NSID registry**
(`templates/lexicons/<NSID>.html`, with a generic fallback). Two surfaces:

- **Lexicon pages**: root-level routes (`/projects` → `sh.tangled.repo`,
  `/books` → `buzz.bookhive.book`, configurable via `LEXICON_PAGES`).
- **Preview mode** (`PREVIEW`): `/@<handle>` renders *any* account's public
  content — profile header plus a section per known collection. Throttled per
  IP, `noindex`. `PREVIEW_LANDING` adds a service landing page + waitlist.

### Reactions (`reactions.py`)

Post pages show Bluesky like/repost counts and the reply thread (as comments),
plus cross-app reaction counts via the **Constellation** backlink index.
Render-path latency is bounded (`REACTIONS_TIMEOUT`) with an optional
cache-only mode (`REACTIONS_BLOCKING`) warmed by `manage.py atproto warm`.

### OAuth client (`atproto/oauth/`, needs the `oauth` extra)

A confidential ATProto OAuth client so visitors sign in with their own
account — the basis for claiming and any write-on-behalf feature.

- `keys.py` — the ES256 client signing key and its published JWKS.
- `metadata.py` — the client-metadata document that *is* the `client_id`.
- `dpop.py` — per-session DPoP proof JWTs (RFC 9449).
- `flow.py` — the flow: PDS → protected-resource metadata → auth-server
  metadata → PAR (PKCE S256 + `private_key_jwt`) → callback code exchange →
  DPoP-bound tokens stored as `OAuthSession`. Also `refresh()` (row-locked
  against concurrent single-use-token spend) and `xrpc_call()` (authenticated
  XRPC with DPoP-nonce handling and refresh-on-401).
- Routes appear under `/oauth/*` only when `OAUTH_CLIENT` is configured.

### Jetstream consumer (`jetstream.py`, needs the `jetstream` extra)

`manage.py atproto jetstream` runs one websocket to a Jetstream instance
(`wantedDids` = owner + active tenants) and invalidates the exact read caches a
commit could have staled. It resumes from a persisted cursor and is **purely an
optimization** — with it off, pages fall back to TTL caches. The ORM/cache
calls are marshalled through `sync_to_async` (they run inside the async loop).

## The hosted layer (`django_mosaic.hosted`)

App label `django_mosaic_hosted`. One deployment, many personal sites. Config
lives under `MOSAIC_HOSTED`; inert until `BASE_DOMAIN` is set. Requires the
`oauth` extra (claiming is gated on OAuth).

### Tenancy and routing (`models.py`, `middleware.py`)

- **`Tenant`** — a thin registry row: `did` (immutable, the identity proven at
  claim), `handle`, `subdomain`, `status`, optional `custom_domain` +
  `domain_verified_at`. All content and site config live in the tenant's *own*
  PDS, so this row plus their handle reproduces the whole site.
- **`TenantMiddleware`** resolves the request's Host — a custom domain (exact
  match) or `<subdomain>.<BASE_DOMAIN>` — to a tenant and swaps in the tenant
  URLconf (`tenant_urls.py`). The base domain and unrelated hosts pass through
  to the normal URLconf. Suspended tenants 404.

### Claiming and the dashboard (`views.py`, `site_settings.py`)

- **`/claim`** — an OAuth-gated flow; a visitor can only claim a site for the
  DID they're signed in as (ownership proven by the grant, not by anything we
  store). One subdomain per DID.
- **`/dashboard`** — arrange home-page sections (show/hide, reorder, retitle),
  pick a theme (presets + validated design tokens → `--mosaic-*` CSS
  variables), and paste custom CSS. The entire configuration is saved as a
  single `blog.mosaic.site.settings` record **in the tenant's own PDS** via
  their OAuth grant; the service DB holds no site config. Read back and
  **re-validated** on the render path (never trust stored data), so a
  hand-edited record can't inject CSS or crash the page.

### The write path (`composer.py`)

- **`/dashboard/write`** publishes a markdown post as a `site.standard.document`
  in the tenant's repo (TID rkey minted locally so the permalink is known
  up front; publication record ensured on first publish; `createRecord` so a
  collision fails loudly). Tenant sites render documents at `/posts/<rkey>`.

### Custom domains and moderation (`views.py`, `middleware.py`, `admin.py`)

- **Custom domains**: a tenant connects a domain, points DNS at
  `DOMAIN_TARGET`, and TLS is issued on demand — `/domains/check` is the Caddy
  `ask` endpoint that authorizes issuance only for registered domains.
  Verification is *operational*: the first request on that Host stamps
  `domain_verified_at`. This trust rests on the ingress (see the security
  model below). Tenant hosts serve `/.well-known/atproto-did` for
  domain-as-handle.
- **Reports**: `/report` files an abuse report (honeypot + `(IP, site)`
  throttle) into a `Report` admin with resolve / suspend-tenant actions;
  suspension takes effect on the next request.

## Cross-cutting concerns

### Settings

Each layer reads a single dict — `MOSAIC_ATPROTO`, `MOSAIC_HOSTED` (plus core
`MOSAIC_PAGE_SIZE`) — merged over per-module `DEFAULTS`. Accessors live in each
`conf.py` (`get_setting`, `as_dict`, `enabled()`, `oauth_enabled()`, etc.).
Several routes are built **at import time** from settings (lexicon pages, the
landing page, OAuth routes), so changing those settings requires a restart —
and tests that exercise them use a private `urlpatterns` + `ROOT_URLCONF`.

### Caching

Reads are cached with short TTLs and **DID-scoped keys**, so nothing leaks
across tenants:

| Key | Written by | TTL |
|---|---|---|
| `mosaic_atproto:identity:{handle}` / `identity_did:{did}` | `identity` | 1h |
| `mosaic_atproto:collections:{did}` | `lexicons.describe_repo` | 10m |
| `mosaic_atproto:records:{did}:{nsid}:{limit}` | `lexicons.list_records` | 5m |
| `mosaic_atproto:profile:{did}` | `preview.fetch_profile` | 10m |
| `mosaic_hosted:settings:{did}` | `hosted.site_settings.load` | 5m |
| `mosaic_hosted:document:{did}:{rkey}` | `hosted.composer.get_document` | 60s |

The Jetstream consumer deletes exactly these keys on a matching commit; the
composer invalidates the collection/records keys on publish. Use a shared cache
backend (Redis) in production so throttles and invalidation work across
workers. `""` is used as a cached-miss sentinel where absence must be cached.

### Security model

- **SSRF**: every PDS / authorization-server / endpoint URL discovered from an
  attacker-controllable document is validated (https, no IP literals, no
  internal hosts) before mosaic connects to it or POSTs credentials.
- **OAuth**: PKCE S256, PAR, DPoP-bound tokens, `private_key_jwt`; the
  callback `iss` is required and checked; refresh is row-locked; token
  material is never rendered in the admin (delete a row to revoke).
- **Tenant isolation**: tenant sites resolve from the immutable claimed DID
  (not the mutable handle); caches are DID-scoped; hostile settings-record
  shapes degrade to defaults rather than 500-ing a public page.
- **Custom-domain verification is operational, not cryptographic.** Mosaic
  marks a domain verified from the `Host` header alone; that is safe **only**
  if the app is reachable exclusively through your TLS proxy (bind to
  `127.0.0.1`). See [`hosted-setup.md`](hosted-setup.md) → "Security model".
- **Theme/CSS**: design tokens are validated against a fixed vocabulary on
  both write and read; custom CSS is served as a standalone `text/css`
  response (never inlined), so it can style but not inject markup.

## Request lifecycles

**A hosted tenant home page** (`GET https://alice.example/`):
1. `TenantMiddleware` matches the Host to a `Tenant`, swaps in `tenant_urls`.
2. `tenant_home` resolves the identity from `tenant.did` (not the handle),
   loads the settings record + collections + records (cached), arranges
   sections per the tenant's config, and renders with theme CSS variables.
3. If the tenant published via the composer, the "Writing" section links to
   `/posts/<rkey>` document pages on the same host.

**Publishing a blog post to ATProto** (admin save):
1. `Post.save()` commits; a `post_save` signal schedules an
   `on_commit` publish (only if the bridge is configured and the post is
   public + published).
2. `publisher.publish_post` uploads the cover blob, builds the
   `site.standard.document`, writes it (and a companion Bluesky post on first
   publish) via the app-password `Session`, and records the mapping.

## Testing and CI

- ~340 tests under `tests/`, all HTTP mocked; suites that need an extra
  `pytest.importorskip` it (so a minimal install still collects cleanly).
- Tests that exercise import-time routes define a module-level `urlpatterns`
  and set `ROOT_URLCONF=__name__`.
- CI (`.github/workflows/ci.yml`): ruff + black, then the suite across
  Python 3.12/3.13 × Django 5.2/6.0. `manage.py atproto check` validates the
  live reaction parsers against the real APIs by hand (the sandbox can't reach
  them).

## Where to look

| To change... | Start in |
|---|---|
| Post model / editor / feeds | `django_mosaic/models.py`, `admin.py`, `views.py`, `feeds.py` |
| How posts sync to the PDS | `atproto/publisher.py`, `atproto/signals.py` |
| A collection page's look | `atproto/templates/lexicons/<NSID>.html` |
| Preview / landing | `atproto/preview.py`, `atproto/views.py` |
| Reactions | `atproto/reactions.py` |
| OAuth sign-in | `atproto/oauth/flow.py`, `views.py` |
| Live cache invalidation | `atproto/jetstream.py` |
| Multi-tenant routing | `hosted/middleware.py`, `hosted/urls.py`, `tenant_urls.py` |
| Claiming / dashboard / composer | `hosted/views.py`, `site_settings.py`, `composer.py` |
| Any setting | the relevant `conf.py` (`DEFAULTS`) |
