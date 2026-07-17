# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Read-only preview mode (`MOSAIC_ATPROTO["PREVIEW"]`): `/@<handle>` renders
  any account's public ATmosphere content — profile header plus sections for
  every known collection present in their repo, other collections listed by
  name. Preview responses are throttled per IP (`PREVIEW_RATE_LIMIT`) and
  marked `noindex`.
- Preview-service mode (`PREVIEW_LANDING`): a landing page at the site root
  with a handle form and a waitlist signup (honeypot-filtered,
  admin-visible), for deploying a dedicated "see your ATmosphere home"
  instance.
- glightbox is vendored (3.3.1, MIT) instead of loaded from the jsdelivr CDN.
- ATProto OAuth client (`MOSAIC_ATPROTO["OAUTH_CLIENT"]`, requires the new
  `oauth` extra): visitors sign in with any ATProto account. Implements the
  atproto OAuth profile — confidential client (`private_key_jwt` + published
  JWKS), PAR, PKCE S256, DPoP-bound tokens with nonce handling and automatic
  refresh. Ships `/oauth/*` routes, an overridable login page, an
  `OAuthSession` model, `flow.xrpc_call()` for authenticated XRPC as the
  signed-in user, and `manage.py atproto oauth-key` for key generation.
- Multi-tenant hosting app (`django_mosaic.hosted`, opt-in via
  `MOSAIC_HOSTED["BASE_DOMAIN"]`): a thin `Tenant` registry (DID ↔
  subdomain), `TenantMiddleware` that routes `<subdomain>.<base>` hosts to
  the tenant's site (rendered live from their PDS, standalone template),
  and an OAuth-gated `/claim` flow with reserved-name and slug validation
  plus admin suspend/reactivate actions.
- Tenant dashboard (`/dashboard`): arrange home-page sections (show/hide,
  retitle, reorder) and pick a theme — presets plus validated design tokens
  (colors, font, radius) emitted as `--mosaic-*` CSS custom properties. The
  whole configuration is stored as a `blog.mosaic.site.settings` record in
  the tenant's own PDS via their OAuth grant; the service database holds no
  site config.
- Custom domains for hosted tenants: connect a domain from the dashboard,
  `/domains/check` on-demand TLS `ask` endpoint (Caddy integration),
  verification implicit in the first served request. Tenant hosts serve
  `/.well-known/atproto-did`, enabling the domain-as-handle wizard shown on
  connected domains.
- Abuse reports: `/report` form (anonymous, honeypot-filtered, per-IP
  throttled) feeding a `Report` admin with resolve and
  suspend-the-reported-tenant actions; tenant page footers link to it.
- Composer (`/dashboard/write`): tenants publish markdown posts as
  `site.standard.document` records straight into their own PDS (TID rkeys
  minted locally, publication record ensured on first publish). Documents
  get `/posts/<rkey>` pages on the tenant site with sanitized markdown
  rendering and a `textContent` fallback for documents from other apps.
- Custom CSS tier for hosted tenants: a dashboard stylesheet textarea
  stored in the settings record and served standalone at `/custom.css`
  (`text/css` + `nosniff`, never inlined into HTML).
- Jetstream consumer (`manage.py atproto jetstream`, new `jetstream`
  extra): one websocket watching the owner plus all active tenants,
  invalidating the relevant read caches the moment an account writes to
  its repo. Cursor-resumed across restarts; purely an optimization over
  the TTL caches.

### Changed
- All ATProto read paths (lexicon pages, blob URLs, record partials) now take
  an explicit identity instead of reading a settings singleton, with
  per-identity caches — groundwork for multi-tenant hosting. Owner DID/PDS
  overrides no longer apply when resolving other handles, and PDS endpoints
  discovered from DID documents are validated (https, no IP literals or
  internal hosts) against SSRF.

### Security (post-review hardening)
- OAuth: the discovered authorization server and every endpoint from its
  metadata are now SSRF-validated (https, no internal/IP hosts) before the
  client POSTs any client assertion to them; a callback missing the required
  `iss` parameter is rejected instead of assumed; token refresh is serialized
  with a row lock so concurrent workers can't burn a rotating refresh token;
  the authenticated-XRPC retry loop no longer conflates a nonce demand with a
  refresh and can't exhaust and mis-report a recoverable error.
- Hosted tenant sites resolve from the immutable claimed DID, not the current
  handle, so a later handle takeover cannot hijack a subdomain's content.
- Jetstream: `wanted_dids` runs via `sync_to_async` (the ORM call silently
  failed in the async loop, dropping every tenant and — with no owner —
  subscribing to the whole firehose); an empty DID set is refused; reconnect
  backs off on clean closes too.
- Composer: a transient PDS error no longer masquerades as "no publication
  record" and overwrites it; documents are written with `createRecord` so a
  TID collision fails loudly; `/posts/<rkey>` rejects non-TID keys before
  touching a cache/PDS; network errors surface as friendly retries, not 500s.
- Site-settings render helpers tolerate arbitrary hostile record shapes
  instead of 500-ing a tenant's public page; custom CSS over the cap is
  rejected rather than silently truncated.
- Custom domains: an unverified registration can be reclaimed by the real
  owner (a squatter can no longer permanently block a domain); only verified
  domains are locked. Concurrent claims/domain writes return a friendly
  conflict instead of a 500. Report throttle keys on `(IP, site)`.

### Security (second review round)
- SSRF: outbound ATProto/OAuth HTTP no longer follows redirects, so a
  validated public endpoint can't 30x-bounce a request (with its client
  assertion or token) to an internal host; endpoint validation now also
  resolves the hostname and rejects names that map to internal/reserved IPs
  (e.g. `*.nip.io`); and the `did:web` document fetch itself is validated,
  not just the endpoint inside the returned document. (Active DNS rebinding
  is still out of scope — noted in `client._resolves_to_public_ip`.)
- Record/collection reads on the public render path use a short
  `READ_TIMEOUT` (default 5s) instead of the 15s publish timeout, so a
  slow/hostile PDS (arbitrary in preview mode) can't tie up a worker across a
  page's sections.
- Jetstream: `handle_event` (which invalidates caches, and via `DatabaseCache`
  touches the ORM) now runs through `sync_to_async` too — a shared DB-backed
  cache would otherwise raise `SynchronousOnlyOperation` on the first event
  and reconnect forever processing nothing.
- Hosted: a pending (unverified) custom-domain registration is protected from
  reclaim until it goes stale (`DOMAIN_RECLAIM_HOURS`, default 72), closing a
  race where an attacker could grab a victim's freshly-pointed domain before
  its first request verified it. Suspended tenants are now locked out of the
  dashboard, composer, and domain settings, not just public serving.
- The private-namespace token gate can no longer be bypassed with URL casing
  (e.g. `/PRIVATE/…`) on case-insensitive database collations: views and the
  RSS feed require an exact-case namespace match.

### Fixed
- CI test matrix now actually varies: `uv run` was re-syncing the environment
  and undoing the per-leg Django pin, and a checked-in `.python-version`
  overrode the interpreter, so every leg silently ran the same Python/Django.
  The matrix pins the interpreter with `--python` and runs with `--no-sync`.
- Migrations: `secret_id` duplicates are regenerated *before* the unique
  constraint is added (0004), and tag-slug backfill de-duplicates per namespace
  (0009), so upgrading an install with existing rows no longer bricks on an
  `IntegrityError`.
- Post permalinks build the year from the active timezone (matching the
  `published_at__year` lookup), so a post near a UTC/local year boundary no
  longer 404s against its own URL under a non-UTC `TIME_ZONE`.
- `settings.CONSTANTS["site"]` title/description now default to empty instead
  of raising `AttributeError`/`KeyError`, so mosaic can be installed into an
  existing project without the scaffold.
- The sitemap derives its excluded namespaces from the auth registry, so a
  second gated namespace (not just the literal `private`) stays out of it.
- Prev/next post navigation breaks ties by id and preloads the namespace, so
  posts sharing a `published_at` still link to each other without extra queries.
- Uploaded images that fail processing are re-stored under a random `.jpg`
  name instead of the user-supplied one, so an undecodable `x.html` payload
  can't be served as active content; a non-numeric `post_id` no longer 500s.
- `list_records` returns already-fetched pages on a mid-pagination failure
  instead of dropping a populated collection to empty; blob URLs escape the
  `cid`; the preview handle is sanitized before use as a cache key.
- OAuth: the token response's `token_type` (must be DPoP) and granted `scope`
  are verified; the session key is rotated on sign-in (anti session-fixation);
  malformed token/PAR responses raise a clean error instead of 500.
- Deployment tooling: `config_manager` is no longer exposed as a broken
  management command; `--host/--user/--domain` flags are honoured; scaffolded
  projects ship a `pyproject.toml` and the deploy WSGI/URLconf defaults match
  the generated flat layout; `validate_config` also covers `ssh_key`,
  `wsgi_module`, `url_conf`, and `gunicorn_workers`; DB backups use sqlite's
  `.backup` (consistent snapshot) and the backup timer's `Requires=` respects
  a custom `app_name`; the Dockerfile installs from `pyproject.toml`.
- Scaffolded settings define `CACHES` (Redis via `REDIS_URL`, else locmem) and
  only trust `X-Forwarded-Proto` when `DEBUG` is off, so the Jetstream consumer
  and throttles work across processes and the proxy header can't be spoofed
  locally.
- Jetstream persists its cursor on a 30s cadence (plus a flush when a
  connection ends) instead of every 100 events, resets its reconnect backoff
  only after a connection proves healthy, and logs a real DB outage instead of
  silently degrading to owner-only.
- `manage.py import` is idempotent (re-import updates rather than duplicates)
  and no longer aborts the whole batch on one row's `IntegrityError`.

## [0.2.0] - 2026-07-03

### Added
- Martor markdown editor in the admin with live preview and toolbar image
  upload; drag-and-drop multi-image upload zone on the post change form
  (uploads create processed `ContentImage` rows immediately).
- Optional ATProto bridge (`django_mosaic.atproto`): published public posts
  sync to your PDS as `site.standard.document` records (with `coverImage` and
  a mosaic-native markdown `content` block) plus a companion Bluesky post;
  well-known endpoints for domain-as-handle and publication verification;
  `manage.py atproto publish|unpublish|status|warm|check`. See
  `docs/atproto-setup.md`.
- ATmosphere reactions on post pages: Bluesky like/repost counts and reply
  thread as the comment section, plus cross-app reaction counts via the
  Constellation backlink index. Bounded render-path latency
  (`REACTIONS_TIMEOUT`) with an optional cache-only mode
  (`REACTIONS_BLOCKING`) warmed by `manage.py atproto warm`.
- Lexicon collection pages rendered straight from your PDS repo
  (`/projects` from `sh.tangled.repo`, `/books` from `buzz.bookhive.book`,
  any collection via `MOSAIC_ATPROTO["LEXICON_PAGES"]`), customizable by
  template override with a generic fallback renderer.
- `mosaic-admin init` console-script scaffolder that generates a complete
  runnable project.
- List pagination (`MOSAIC_PAGE_SIZE`, default 10) on home/list/tag pages.
- CI (GitHub Actions: ruff + black + tests across Python 3.12/3.13 × Django
  5.2/6.0), lint/format config, `py.typed` markers, and unit tests for the
  previously-untested deployment tooling.

### Fixed
- Shipped settings template now installs `MagicAuthorizationMiddleware`
  (private namespace was unprotected in generated projects); a system
  check (`django_mosaic.W001`) warns when it is missing.
- Draft preview URLs are scoped to their namespace; post detail honors the
  year segment; unknown namespaces 404.
- "Save and publish" actually publishes; published posts always get slugs;
  RGBA/palette images convert cleanly to JPEG and reprocess on change; tag
  slugs are unique per namespace; N+1 queries removed across views/admin.
- `import` command works again (dependencies, `--author`, YAML list tags,
  timezone-aware dates).
- Deployment tooling validates config values against shell injection and
  redacts secrets from terminal output; production settings default closed
  (DEBUG off, required SECRET_KEY, secure cookies/HSTS/proxy header).
- Deleting a `ContentImage` (or its `Post`) now removes the stored image
  files instead of orphaning them.

### Changed
- `Post.published_version` is now a `ForeignKey` to `reversion.Version`
  (`SET_NULL`) instead of a raw integer id; the migration preserves existing
  pinned pointers.
- `fabric` moved to the optional `deploy` extra
  (`pip install django-mosaic[deploy]`); `bleach` dependency dropped;
  `markdown`/`pyyaml`/`python-dateutil`/`requests` declared; Django capped
  `<7`; markdown sanitization enabled by default in generated settings.

### Migration notes
- Upgrading from an existing install: run `python manage.py migrate`. The
  `published_version` change is a state-only migration and preserves data.
- The private namespace requires `MagicAuthorizationMiddleware` in
  `MIDDLEWARE`; add it if you are not using the shipped settings template.

## [0.1.4] - 2026-02-07

### Added
- Automated deployment system with `uv run python manage.py mosaic deployment setup` command
- VPS deployment with Docker containerization
- Nginx reverse proxy configuration with rate limiting
- Automatic SSL certificate provisioning via Let's Encrypt/certbot
- UFW firewall configuration
- Systemd service management for application and backups
- Automated hourly database backups with retention policy (hourly/daily/weekly/monthly)
- Deployment status checker with `uv run python manage.py mosaic deployment status`
- Persistent deployment configuration saved to `.deployment-config.toml`
- Interactive deployment wizard with validation
- Deployment modes: `--auto`, `--explain`, `--dry-run`
- Health checks for services, Docker containers, SSL certificates, and application availability

