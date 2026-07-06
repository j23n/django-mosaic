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

### Changed
- All ATProto read paths (lexicon pages, blob URLs, record partials) now take
  an explicit identity instead of reading a settings singleton, with
  per-identity caches — groundwork for multi-tenant hosting. Owner DID/PDS
  overrides no longer apply when resolving other handles, and PDS endpoints
  discovered from DID documents are validated (https, no IP literals or
  internal hosts) against SSRF.

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

