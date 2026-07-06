# ATProto Bridge — Setup Guide

This is the practical setup guide for the `django_mosaic.atproto` bridge. For
the "why" — the architecture, the reframe of your PDS as the canonical store,
and the protocol research behind these choices — see
[`atproto-design.md`](./atproto-design.md). This document does not repeat that
background; it tells you how to turn the bridge on and operate it.

## What the bridge does

When enabled, publishing a public post syncs it to your ATProto PDS as a
`site.standard.document` record (under a `site.standard.publication` record for
your site), optionally alongside a companion `app.bsky.feed.post` that embeds
the post's canonical URL. Other apps in the ATmosphere can then see, index,
link, and reply to your posts. Those inbound reactions and comments — the
companion post's Bluesky reply thread plus cross-app counts from the
Constellation backlink index — are pulled back in and rendered on the post's
page. Separately, any other lexicon collection in your repo (Tangled repos,
BookHive books, and so on) can be rendered as a root-level page. The bridge is
**inert until you configure it**, so installing the app never changes the
behavior of a plain mosaic site.

## Prerequisites

You need:

1. **An ATProto account** — either a Bluesky account (`something.bsky.social`)
   or an account on a self-hosted PDS.
2. **Your handle** — e.g. `alice.bsky.social`, or your own domain if you set up
   [domain-as-handle](#domain-as-handle) below.
3. **An app password** — a scoped credential separate from your main password.

### Creating an app password

For a Bluesky account:

1. Open the Bluesky app or go to <https://bsky.app/settings/app-passwords>
   (**Settings → Privacy and Security → App Passwords**).
2. Add a new app password, give it a name (e.g. `django-mosaic`), and copy the
   generated value. You only see it once.

> **Security warning.** Bluesky app passwords are **full-access** credentials —
> the bridge authenticates with `com.atproto.server.createSession`, and an app
> password can read and write your entire repo, not just the collections mosaic
> touches. Treat it like a root password:
>
> - **Store it in an environment variable**, never inline in your Django
>   settings or committed to version control.
> - Revoke it from the App Passwords screen if it is ever exposed.
> - See the design doc's *Identity & plumbing* and *Private content* sections
>   for the longer-term OAuth-with-granular-scopes plan.

## Finding your DID (optional)

The bridge resolves your DID automatically from your handle, so **you normally
do not need to set it**. It is exposed as an optional override only for cases
where handle resolution is unavailable or you want to pin it.

If you do want to look it up:

- Visit `https://bsky.app/profile/<your-handle>` — the DID (`did:plc:...`) is
  shown on the profile / in its metadata.
- Or call the API directly:

  ```bash
  curl "https://bsky.social/xrpc/com.atproto.identity.resolveHandle?handle=<your-handle>"
  ```

If you pin `DID` yourself and are **not** using the default Bluesky PDS, also
set `PDS_URL` (repo reads need both when the handle is not resolved).

## Installation

### 1. Add the app to `INSTALLED_APPS`

```python
INSTALLED_APPS = [
    # ...
    "django_mosaic",
    "django_mosaic.atproto",
]
```

The app label is `django_mosaic_atproto` (relevant for migrations and any
`--app` flags).

### 2. Wire up the URLs — before the mosaic catch-all

Include the bridge's URLconf **at your project root** so the well-known paths
land on the domain root, and place it **before** mosaic's namespace catch-all:

```python
# project urls.py
urlpatterns = [
    path("", include("django_mosaic.atproto.urls")),  # must come first
    # ... your other routes ...
    path("", include("django_mosaic.urls")),           # mosaic's catch-all
]
```

The atproto URLconf registers `.well-known/atproto-did`,
`.well-known/site.standard.publication`, and **one root-level route per
configured lexicon page** (e.g. `/projects`, `/books`). Mosaic's namespace
routing matches slugs greedily at the root, so if it is included first it will
swallow `/projects` before the bridge ever sees it. Ordering the bridge first
lets these specific routes win.

### 3. Run migrations

```bash
python manage.py migrate django_mosaic_atproto
```

### 4. Configure `MOSAIC_ATPROTO`

All configuration lives in a single `MOSAIC_ATPROTO` dict. The bridge stays
inert (`enabled()` is `False`) until both `HANDLE` and `APP_PASSWORD` are set —
so this dict is where you turn it on. A complete, copy-pasteable example:

```python
import os

MOSAIC_ATPROTO = {
    # --- identity (required to enable) ---
    "HANDLE": "example.com",                       # your atproto handle
    "APP_PASSWORD": os.environ["ATPROTO_APP_PASSWORD"],

    # --- optional identity overrides (auto-resolved from HANDLE) ---
    "PDS_URL": "",                                  # resolved from the DID
    "DID": "",                                      # resolved from the handle

    # --- your publication ---
    "PUBLICATION": {
        "NAME": "My Blog",
        "URL": "https://example.com",
        "DESCRIPTION": "",
    },

    # --- publishing behavior ---
    "NAMESPACES": ["public"],                       # namespaces that sync
    "AUTO_PUBLISH": True,                           # sync on save
    "COMPANION_POST": True,                          # also create a Bluesky post
    "COMPANION_TEXT": "New post: {title}\n\n{url}",

    # --- root-level lexicon pages ---
    "LEXICON_PAGES": {
        "projects": {"collection": "sh.tangled.repo", "title": "Projects"},
        "books": {"collection": "buzz.bookhive.book", "title": "Books"},
    },
}
```

Read the app password from `os.environ` (set `ATPROTO_APP_PASSWORD` in your
process environment / secrets manager). Do not hardcode it.

## Domain-as-handle

The `/.well-known/atproto-did` endpoint (registered by the bridge's URLconf)
lets **your blog's own domain be your ATProto handle**. Instead of
`example.bsky.social`, your identity and your site share one domain, and your
content addresses become `at://example.com/site.standard.document/...`.

ATProto resolves a domain handle by fetching `did:...` from either a DNS TXT
record at `_atproto.<domain>` **or** this `/.well-known/atproto-did` route. With
the bridge installed at the project root, that route is already served for you —
you set your handle to your domain in your PDS/Bluesky account settings, and the
well-known endpoint provides the verification. See the design doc's
*Identity & plumbing* section for the resolution chain.

## Publishing

There are two ways posts reach the PDS.

### Auto-publish on save

With `AUTO_PUBLISH` set to `True` (the default), a post is synced automatically
when it is saved in a published, syncable state (a `post_save` signal drives
this). Saving an update re-pushes the same record.

### Manual / backfill via management command

Use the `atproto` management command to sync existing content or operate by
hand:

```bash
# Sync every syncable post (backfill)
python manage.py atproto publish

# Sync (or re-sync) just one post by id
python manage.py atproto publish --post 42

# Remove a post's records from the PDS
python manage.py atproto unpublish --post 42

# Also delete the companion Bluesky post
python manage.py atproto unpublish --post 42 --delete-companion

# Show configuration + tracking status
python manage.py atproto status
```

Notes on what syncs:

- A post is **syncable** only if the bridge is enabled, the post
  `is_published`, and its namespace is listed in `NAMESPACES` (default
  `["public"]`).
- **Drafts never sync** (they are not published).
- The **private namespace never syncs** — it is not in `NAMESPACES`, and by
  design private content stays off-protocol in Django (see the design doc's
  *Private content* section).
- `publish` skips non-syncable posts with a note rather than failing.
- The companion post is created **once** per document and kept across updates;
  `unpublish` leaves it alone unless you pass `--delete-companion`.
- The command requires configuration for `publish`/`unpublish`; `status` works
  even when the bridge is disabled (it reports `Configured: False`).

## Reactions and comments

On the page of a synced post, reactions appear **automatically** — no
client-side JavaScript widget. The bridge fetches, server-side and cached:

- **The companion Bluesky post's thread** via `app.bsky.feed.getPostThread`
  (unauthenticated, against the public AppView). This yields the like / repost /
  reply counts and the flattened reply tree that renders as the comment section.
  Cached ~5 minutes.
- **Cross-app reaction counts** via the Constellation backlink index — any
  record in any lexicon linking to your document's AT-URI or its canonical URL
  (standard.site recommends, Leaflet comments, WhiteWind comments, Tangled
  stars, Grain favorites, and so on). Recognized collections get a friendly
  label; anything else is summarized under its NSID. Cached ~10 minutes. The
  companion post's own likes/reposts are dropped here to avoid double-counting
  the AppView numbers.

Every fetch **degrades gracefully**: if the AppView or Constellation is down or
returns an unexpected shape, that section simply renders without the data. A
third-party index being unavailable will never 500 a post page.

## Lexicon pages

Beyond your own posts, the bridge can render any other collection in your repo
as a root-level page — the "personal AppView" half of the bridge. This uses two
unauthenticated XRPC reads (`com.atproto.repo.listRecords`) plus a template; it
needs no models and no migrations, and reading only requires an identity (a
`HANDLE`, or a `DID` + `PDS_URL`), not the app password.

By default two pages are configured:

- `/projects` → `sh.tangled.repo` (title "Projects")
- `/books` → `buzz.bookhive.book` (title "Books")

### Adding or customizing collections

Set `LEXICON_PAGES` in `MOSAIC_ATPROTO`. Each entry maps a URL slug to a
collection NSID and a title:

```python
MOSAIC_ATPROTO = {
    # ...
    "LEXICON_PAGES": {
        "projects": {"collection": "sh.tangled.repo", "title": "Projects"},
        "books": {"collection": "buzz.bookhive.book", "title": "Books"},
        "photos": {"collection": "social.grain.gallery", "title": "Photos"},
    },
}
```

Setting `LEXICON_PAGES` **replaces** the defaults (it is not merged), so include
the default entries you want to keep. Set it to `{}` to have no lexicon pages.
Each slug becomes a root-level route, so the URL ordering rule above still
applies. Records are listed newest-first (rkeys are TIDs), capped at 500 per
collection, and cached ~5 minutes.

### Overriding templates

Records render through a template-per-NSID registry, resolved from your
project's template directories (the same override mechanism as the rest of
mosaic):

- `lexicons/<collection NSID>.html` — the per-record partial (e.g.
  `lexicons/sh.tangled.repo.html`). Unknown collections fall back to a generic
  dump.
- `lexicon-page.html` — the whole page wrapper.

Because records are passed through as raw JSON, templates read fields directly
(`{{ record.value.title }}`) and missing/evolved fields simply render blank.

## Sign in with ATProto (OAuth)

The bridge includes a full ATProto OAuth client, so *visitors* can sign in to
your mosaic instance with their own ATProto account (any Bluesky handle or
self-hosted PDS). This is separate from the owner app-password bridge above:
the app password publishes *your* posts; OAuth authenticates *other people*
(or you, without sharing a password) — the building block for site claiming
and acting on a user's behalf within the granted scope.

It implements the ATProto OAuth profile: a confidential client
(`private_key_jwt` with a published JWKS), pushed authorization requests
(PAR), PKCE (S256), and DPoP-bound tokens with automatic nonce handling and
token refresh.

### Setup

1. Install the extra: `pip install django-mosaic[oauth]` (pulls in `pyjwt` +
   `cryptography`).
2. Generate a client signing key and store it as a secret (env var, secret
   manager — never in settings files):

   ```
   python manage.py atproto oauth-key
   ```

3. Configure the client. `BASE_URL` must be the public **https** origin the
   instance is served from — authorization servers fetch
   `<BASE_URL>/oauth/client-metadata.json` to validate the client, so
   localhost won't work in production:

   ```python
   MOSAIC_ATPROTO = {
       # ...
       "OAUTH_CLIENT": {
           "BASE_URL": "https://example.com",
           "PRIVATE_KEY": os.environ["MOSAIC_OAUTH_PRIVATE_KEY"],
       },
   }
   ```

4. Restart. The routes are built at startup (like the lexicon pages):
   `/oauth/client-metadata.json`, `/oauth/jwks.json`, `/oauth/login`,
   `/oauth/callback`, and `/oauth/logout` (POST).

Visitors go to `/oauth/login`, enter their handle, and are redirected to
their own authorization server to approve. Sessions are stored in the
`OAuthSession` model (one row per DID; the admin shows who is connected and
deleting a row revokes server-side use). The login page template is
`atproto/oauth-login.html` — override it to match your branding.

Programmatic use, e.g. reading or writing records as the signed-in user:

```python
from django_mosaic.atproto.oauth import flow

session = flow.current_session(request)  # None if not signed in
if session:
    flow.xrpc_call(
        session, "com.atproto.repo.listRecords",
        params={"repo": session.did, "collection": "app.bsky.feed.like"},
    )
```

`xrpc_call` refreshes expired access tokens automatically and handles the
DPoP nonce dance. Note the tokens and per-session DPoP keys in `OAuthSession`
are secrets — protect the database accordingly.

## Reference: `MOSAIC_ATPROTO` keys

Defaults are taken from `conf.py` (`DEFAULTS`) and the lexicon/reaction modules.

| Key | Default | Meaning |
|---|---|---|
| `HANDLE` | `""` | Your ATProto handle. **Required** to enable the bridge. |
| `APP_PASSWORD` | `""` | App password for `createSession`. **Required** to enable. Read from an env var. |
| `PDS_URL` | `""` | PDS endpoint. Optional; resolved from the DID. Set it if you pin `DID` on a non-default PDS. |
| `DID` | `""` | Your `did:plc:...`. Optional; auto-resolved from `HANDLE`. |
| `PUBLICATION` | `{}` | Your publication metadata. Sub-keys: `NAME`, `URL` (site base URL), `DESCRIPTION`. |
| `NAMESPACES` | `["public"]` | Mosaic namespaces whose published posts sync to the PDS. The private namespace is intentionally excluded. |
| `AUTO_PUBLISH` | `True` | Sync a post to the PDS automatically on save when it is published and syncable. |
| `COMPANION_POST` | `True` | Also create an `app.bsky.feed.post` (external embed of the canonical URL) when first publishing a document. |
| `COMPANION_TEXT` | `"New post: {title}\n\n{url}"` | Template for the companion post text. `{title}` and `{url}` are substituted. Truncated to 300 graphemes. |
| `TIMEOUT` | `15` | Timeout (seconds) for publish/write XRPC calls (run from management commands). |
| `REACTIONS_TIMEOUT` | `3` | Timeout (seconds) for reaction fetches on the public render path — kept short so a slow/down API can't hang a post page. |
| `REACTIONS_BLOCKING` | `True` | When `False`, post pages use only cached reactions (never a live call); warm the cache out of band with `manage.py atproto warm`. Recommended for high-traffic sites. |
| `CONTENT_NSID` | `"blog.mosaic.content.markdown"` | `$type` of the mosaic-native markdown block embedded in the document's open `content` union. Override with your own domain-based NSID if you publish a lexicon for it. |
| `CONTENT_MAX_INLINE_BYTES` | `30000` | Skip the inline content block above this size to avoid 413s against the PDS record limit; `textContent` still carries the post. |
| `LEXICON_PAGES` | `{"projects": {...sh.tangled.repo...}, "books": {...buzz.bookhive.book...}}` | Root-level pages rendering repo collections (`{slug: {collection, title}}`). Setting it replaces the defaults. |
| `PREVIEW` | `False` | Opt-in read-only preview: `/@<handle>` renders any account's public ATmosphere content (profile header + sections for known collections). Public data only; responses are `noindex`. |
| `PREVIEW_LANDING` | `False` | Serve a landing page (handle form + waitlist signup) at the site root, turning the instance into a dedicated preview service. Requires `PREVIEW`. Route is built at startup — restart after changing. |
| `PREVIEW_RATE_LIMIT` | `30` | Max preview loads per client IP per minute (in-app, cache-based; `0` disables). Keys on `REMOTE_ADDR` — behind a proxy, configure real-IP forwarding (e.g. nginx `real_ip`). |
| `OAUTH_CLIENT` | `{...}` (inert) | ATProto OAuth client config; see [Sign in with ATProto](#sign-in-with-atproto-oauth). Sub-keys: `BASE_URL` (**required** to enable), `PRIVATE_KEY` (**required**; PEM ES256 key), `KEY_ID` (`"mosaic-oauth-1"`), `SCOPE` (`"atproto transition:generic"`). |
| `JETSTREAM_URL` | `"wss://jetstream2.us-east.bsky.network/subscribe"` | Jetstream endpoint for `manage.py atproto jetstream` — a long-running consumer (requires the `jetstream` extra) that invalidates read caches the moment a watched account writes to its repo. Optional; without it caches simply expire on their TTLs. |
| `APPVIEW_URL` | `"https://public.api.bsky.app"` | Override the Bluesky AppView used for `getPostThread`. Rarely needed. |
| `CONSTELLATION_URL` | `"https://constellation.microcosm.blue"` | Override the Constellation backlink index. Rarely needed (e.g. self-hosted). |

### Running a preview service

To deploy an instance whose only job is previewing handles ("type a handle,
see their home"): enable `PREVIEW` and `PREVIEW_LANDING`, leave
`HANDLE`/`APP_PASSWORD` unset (no publishing), and use a shared cache backend
(e.g. Redis) in production so the rate limiter works across workers. The
landing page collects waitlist signups into the `WaitlistSignup` model
(visible in the admin). Preview pages are throttled per IP and marked
`noindex` since they render other people's content.

The document `coverImage` is set from the post's featured thumbnail (uploaded
once and reused for the companion post embed). A core mosaic setting,
`MOSAIC_PAGE_SIZE` (default `10`), controls list pagination independent of the
bridge.

Fixed NSIDs used by the bridge (not configurable): documents are
`site.standard.document`, the publication is `site.standard.publication`, and
the companion post is `app.bsky.feed.post`.

## Troubleshooting

**The bridge seems to do nothing (silent, no records appear).**
The bridge is inert until configured. Both `HANDLE` **and** `APP_PASSWORD` must
be set — with either missing, `enabled()` is `False` and no sync, signal, or
management command action runs. Confirm with:

```bash
python manage.py atproto status
```

It prints `Configured: True/False`, your handle, whether a publication record is
tracked, and how many documents are tracked locally.

**Publishing fails or a post isn't syncing.**

- Run `python manage.py atproto status` to confirm configuration and check
  whether the publication record exists.
- If `publish` reports a post as "not syncable", verify it is published and its
  namespace is in `NAMESPACES` (drafts and the private namespace never sync).
- Turn on logging for the logger **`django_mosaic.atproto`** to see publish,
  update, delete, and warning messages (blob-read failures, XRPC errors, etc.):

  ```python
  LOGGING = {
      "version": 1,
      "loggers": {
          "django_mosaic.atproto": {"handlers": ["console"], "level": "INFO"},
      },
      # ... handlers ...
  }
  ```

**Reactions or lexicon pages are empty / look off.**
These paths degrade gracefully by design — an empty section usually means the
AppView, Constellation, or your PDS was unreachable or returned an unexpected
shape (check the `django_mosaic.atproto` warnings). Note that the
Constellation `/links/all` and Bluesky `getPostThread` response parsers were
written against the **documented** API shapes; those services evolve, so if
counts or threads render incorrectly, sanity-check the parsers
(`reactions.py`: `_parse_constellation`, `_flatten_replies`) against the live
responses before assuming a config problem.
