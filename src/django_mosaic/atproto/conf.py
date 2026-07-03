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
    # Timeout (seconds) for every XRPC HTTP call.
    "TIMEOUT": 15,
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
