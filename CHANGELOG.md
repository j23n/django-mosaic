# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Martor markdown editor in the admin with live preview and toolbar image
  upload; drag-and-drop multi-image upload zone on the post change form
  (uploads create processed `ContentImage` rows immediately).
- Optional ATProto bridge (`django_mosaic.atproto`): published public posts
  sync to your PDS as `site.standard.document` records with a companion
  Bluesky post; well-known endpoints for domain-as-handle and publication
  verification; `manage.py atproto publish|unpublish|status`.
- ATmosphere reactions on post pages: Bluesky like/repost counts and reply
  thread as the comment section, plus cross-app reaction counts via the
  Constellation backlink index.
- Lexicon collection pages rendered straight from your PDS repo
  (`/projects` from `sh.tangled.repo`, `/books` from `buzz.bookhive.book`,
  any collection via `MOSAIC_ATPROTO["LEXICON_PAGES"]`), customizable by
  template override with a generic fallback renderer.

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

### Changed
- `fabric` moved to the optional `deploy` extra
  (`pip install django-mosaic[deploy]`); `bleach` dependency dropped;
  markdown sanitization enabled by default in generated settings.

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

