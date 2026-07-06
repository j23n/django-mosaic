# Mosaic

A self-hosted blog in the spirit of the <a href="https://indieweb.org">IndieWeb</a> that grows into a **personal AppView of your [AT Protocol](https://atproto.com) repository** — your posts, photos, repos, books, and other ATmosphere content, rendered as one site you control. Get up and running fast with your own, easily customizable CMS.

### Three ways to run it

Each shape is a superset of the previous one. You opt in by adding an app to `INSTALLED_APPS`; nothing turns on until you configure it.

1. **Blog** (`django_mosaic`) — a self-hosted markdown blog: admin editor, public/private namespaces, RSS, sitemaps. No network dependencies. *Start here.*
2. **Aggregator** (`+ django_mosaic.atproto`) — publish posts to your PDS, render your other lexicon collections as pages, show ATmosphere reactions, preview any handle, sign in with ATProto. See [`docs/atproto-setup.md`](docs/atproto-setup.md).
3. **Hosted** (`+ django_mosaic.hosted`) — one deployment serves many people's sites, each claimed and themed from their own PDS. See [`docs/hosted-setup.md`](docs/hosted-setup.md).

New to the codebase? Read [`docs/architecture.md`](docs/architecture.md) for the map.

## Installation

First, install the package using your favorite python package manager

```bash
uv add django-mosaic
```

or

```bash
pip install django-mosaic
```

### Fastest start: scaffold a project

If you don't already have a Django project, generate a complete, runnable one:

```bash
mosaic-admin init myblog
cd myblog
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

This writes a flat project (`manage.py`, `settings.py`, `urls.py`, `wsgi.py`,
`asgi.py`, a `.env`, and starter templates) already configured for mosaic.

### Add to an existing project

Enable the app in your Django project.

```python
# settings.py
INSTALLED_APPS = [
  ...
  "django_mosaic"
  ...
]
```

Then run the migrations to add the relevant schemas to your database

```bash
uv run python manage.py migrate
```

## Quickstart

Start the development server.

```bash
uv run python manage.py runserver
```

Mosaic exposes all its features through the admin. First, create a user for the admin.

```bash
uv run python manage.py createsuperuser
```

Go to [http://localhost:8000/admin](http://localhost:8000/admin).

![](docs/img/01-admin.png)

You can write a post right within the admin, in [markdown](https://daringfireball.net/projects/markdown/).

![](docs/img/02-create-post.png)

By default, there are two `namespace`s, `public` and `private`. Everything you post in `public` will be visible to, well, everyone. Posts in `private` will be visible only to those with a secret `AccessToken`, which you can also generate in the admin.

Only `Post`s with the `is_draft` flag set to `False` will be shown on your website.

Hit the save button and go to https://localhost:8000

![](docs/img/03-home.png)

Have fun!

## ATProto / ATmosphere (optional)

Mosaic can act as a bridge to the [AT Protocol](https://atproto.com): published
public posts sync to your PDS as [standard.site](https://standard.site)
documents with a companion Bluesky post, replies to that post render as your
comment section, and cross-app reactions (recommends, stars, favorites) are
counted via the Constellation backlink index. Your other ATProto content —
[Tangled](https://tangled.org) repositories, [BookHive](https://bookhive.buzz)
books, or any lexicon collection — can be rendered as pages on your site
straight from your repo, no local models needed.

Enable it by adding `django_mosaic.atproto` to `INSTALLED_APPS`, including
`django_mosaic.atproto.urls` at your project root, and configuring the
`MOSAIC_ATPROTO` setting (see `django_mosaic/atproto/conf.py` for all
options):

```python
MOSAIC_ATPROTO = {
    "HANDLE": "example.com",
    "APP_PASSWORD": os.environ["ATPROTO_APP_PASSWORD"],
    "PUBLICATION": {"NAME": "My Blog", "URL": "https://example.com"},
}
```

Customize collection pages (e.g. `/projects`) by overriding
`lexicons/<collection NSID>.html` — the same template-override mechanism as
the rest of mosaic. Drafts and the private namespace never leave your server.

A few optional pieces build on the bridge, each behind an extra:

- **Preview mode** (`MOSAIC_ATPROTO["PREVIEW"]`) renders *any* handle's public
  ATmosphere content at `/@<handle>` — a demo of the aggregator, and the seed
  of the hosted service's "type a handle, see your home" landing page.
- **Sign in with ATProto** (`pip install django-mosaic[oauth]`) is a full
  OAuth client so visitors authenticate with their own account — the basis for
  claiming a hosted site.
- **Live cache invalidation** (`pip install django-mosaic[jetstream]`,
  `manage.py atproto jetstream`) keeps pages fresh seconds after you publish
  anywhere in the ATmosphere, instead of at cache TTL.

See [`docs/atproto-setup.md`](docs/atproto-setup.md) for the full settings
reference and setup walkthrough.

## Deployment

To reduce one of the major pains of running your own site, Mosaic includes automated deployment for VPS hosting with sane (reach out if not!) defaults for Docker, nginx, and SSL certificates.

### Quick Deploy

Deploy to a fresh Ubuntu/Debian VPS:

```bash
uv run python manage.py mosaic deployment setup
```

The setup wizard will prompt you for:
- VPS hostname and SSH credentials
- Domain name
- Email for SSL certificate notifications

The deployment script will:
- Install Docker, nginx, and certbot
- Configure firewall (UFW)
- Build and deploy your application in a Docker container
- Set up nginx as a reverse proxy with rate limiting
- Obtain SSL certificates via Let's Encrypt
- Configure automated hourly database backups

### Check Deployment Status

```bash
uv run python manage.py mosaic deployment status
```

Shows health checks for services, SSL certificates, backups, and application availability.

### Configuration

Configuration is saved to `.deployment-config.toml` for subsequent runs.


## Documentation

- [`docs/architecture.md`](docs/architecture.md) — how the three layers fit
  together, the data model, request lifecycles, caching, and the security model.
- [`docs/atproto-setup.md`](docs/atproto-setup.md) — enabling the ATProto
  bridge: publishing, lexicon pages, reactions, preview mode, OAuth sign-in,
  and the full `MOSAIC_ATPROTO` reference.
- [`docs/hosted-setup.md`](docs/hosted-setup.md) — running the multi-tenant
  hosted service: routing, claiming, the dashboard, custom domains, and
  moderation.
- [`docs/atproto-design.md`](docs/atproto-design.md) /
  [`docs/hosted-plan.md`](docs/hosted-plan.md) — the design notes and product
  plan behind the ATProto and hosted directions.

## 🤖 AI Disclaimer

This project uses AI-assisted development tools. See the [AI usage policy](https://j23n.com/public/posts/2026/my-ai-policy) for details.

**Tools**

- Claude Code (Anthropic) · `claude-sonnet-4-6` · Agentic

### Contribution Profile

```
Phase                               Human│ AI
─────────────────────────────────────────┼───────────────
Requirements & Scope       95% ██████████│             5%
Architecture & Design      95% ██████████│             5%
Implementation             40%       ████│░░░░░░      60%
Testing                     5%           │░░░░░░░░░░  95%
Documentation              40%       ████│░░░░░░      60%
```

**Oversight**: Collaborative

Human and AI co-author decisions; human reviews all output.

### Process

AI agent operated autonomously across multi-step tasks. Human reviewed diffs, resolved conflicts, and approved merges.

### Accountability

The human author(s) are solely responsible for the content, accuracy, and fitness-for-purpose of this project.

---
*Last updated: 2026-02-20 · Generated with [ai-disclaimer](https://github.com/j23n/ai-disclaimer)*
