"""Settings access for the ATProto bridge.

All configuration lives under a single ``MOSAIC_ATPROTO`` dict so consumers can
opt in with one settings block. The bridge is inert (``enabled() == False``)
until HANDLE and APP_PASSWORD are configured, so installing the app never
breaks a plain mosaic site.

Example::

    MOSAIC_ATPROTO = {
        "HANDLE": "example.com",              # your atproto handle
        "APP_PASSWORD": os.environ["ATPROTO_APP_PASSWORD"],
        "PDS_URL": "",                        # optional; resolved from the DID
        "DID": "",                            # optional; resolved from HANDLE
        "PUBLICATION": {
            "NAME": "My Blog",
            "URL": "https://example.com",
            "DESCRIPTION": "",
        },
        "NAMESPACES": ["public"],             # namespaces that sync to the PDS
        "AUTO_PUBLISH": True,                 # publish on save when is_published
        "COMPANION_POST": True,               # create an app.bsky.feed.post
        "COMPANION_TEXT": "New post: {title}\n\n{url}",
        # Root-level pages rendering collections from your repo. Customize a
        # page by overriding lexicons/<collection NSID>.html (one record),
        # lexicons/pages/<slug>.html (whole page), or lexicon-page.html in
        # your project templates.
        "LEXICON_PAGES": {
            "projects": {"collection": "sh.tangled.repo", "title": "Projects"},
            "books": {"collection": "buzz.bookhive.book", "title": "Books"},
        },
    }
"""

from django.conf import settings

DEFAULTS = {
    "HANDLE": "",
    "APP_PASSWORD": "",
    "PDS_URL": "",
    "DID": "",
    "PUBLICATION": {},
    "NAMESPACES": ["public"],
    "AUTO_PUBLISH": True,
    "COMPANION_POST": True,
    "COMPANION_TEXT": "New post: {title}\n\n{url}",
    # Timeout (seconds) for publish/write XRPC calls (run from mgmt commands).
    "TIMEOUT": 15,
    # Timeout (seconds) for reaction fetches on the public render path. Kept
    # short so a slow/down PDS or index can't hang a post page for readers.
    "REACTIONS_TIMEOUT": 3,
    # When False, the render path only ever reads cached reactions (never
    # makes a live call) and returns nothing on a miss. Warm the cache out of
    # band with `manage.py atproto warm`. Recommended for high-traffic sites.
    "REACTIONS_BLOCKING": True,
    # NSID for the mosaic-native rich-content block embedded in the document's
    # open `content` union. Preserves the source markdown in your repo (so a
    # mosaic AppView or re-import reconstructs the post faithfully); other
    # AppViews fall back to textContent. Override with your own domain-based
    # NSID if you publish a lexicon for it.
    "CONTENT_NSID": "blog.mosaic.content.markdown",
    # Skip the inline content block above this size (bytes) to avoid 413s
    # against the PDS record-size limit; textContent still carries the post.
    "CONTENT_MAX_INLINE_BYTES": 30_000,
    # Opt-in read-only preview mode: /@<handle> renders any account's public
    # ATmosphere content. Public data only; consider rate limiting at the
    # proxy if you enable this on an internet-facing instance.
    "PREVIEW": False,
    # Serve a landing page (handle input + optional waitlist) at the site
    # root, turning this instance into a dedicated preview service. Only
    # honored when PREVIEW is also enabled.
    "PREVIEW_LANDING": False,
    # Max preview page loads per client IP per minute (0 disables the
    # in-app throttle). Behind a reverse proxy, make sure the proxy sets the
    # real client address (e.g. nginx real_ip) — the throttle keys on
    # REMOTE_ADDR.
    "PREVIEW_RATE_LIMIT": 30,
}

DOCUMENT_NSID = "site.standard.document"
PUBLICATION_NSID = "site.standard.publication"
BSKY_POST_NSID = "app.bsky.feed.post"


def get_setting(name):
    conf = getattr(settings, "MOSAIC_ATPROTO", {})
    return conf.get(name, DEFAULTS[name])


def as_dict():
    """The merged configuration (defaults overlaid with project settings)."""
    return {**DEFAULTS, **getattr(settings, "MOSAIC_ATPROTO", {})}


def enabled():
    return bool(get_setting("HANDLE") and get_setting("APP_PASSWORD"))


def publication_url():
    pub = get_setting("PUBLICATION")
    return (pub.get("URL") or "").rstrip("/")
