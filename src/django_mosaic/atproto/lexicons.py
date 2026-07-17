"""Read arbitrary lexicon collections from a repo and render them.

This is the "personal AppView" half of the bridge: any collection in a PDS
repo (Tangled repos, BookHive books, ...) can be listed on the site with two
XRPC reads and a template. No models, no migrations — records are rendered
straight from their JSON via a template-per-NSID registry:

    templates/lexicons/<collection NSID>.html   (record partial; falls back
    templates/lexicons/generic.html              to a raw-ish dump)

Pages are configured in MOSAIC_ATPROTO["LEXICON_PAGES"]; consumers customize
a page by overriding its partial (or `lexicon-page.html`) in their project's
template directory — the same override mechanism as the rest of mosaic.

All read functions accept an explicit ``identity`` (see identity.py) and
default to the site owner's, so the same code paths serve the owner's site
and read-only previews of any handle.
"""

import logging

from django.core.cache import cache

from . import conf
from . import identity as identity_mod
from .client import xrpc_get

logger = logging.getLogger("django_mosaic.atproto")

LIST_CACHE_SECONDS = 300
DESCRIBE_CACHE_SECONDS = 600
MAX_RECORDS = 500

DEFAULT_PAGES = {
    "projects": {"collection": "sh.tangled.repo", "title": "Projects"},
    "books": {"collection": "buzz.bookhive.book", "title": "Books"},
}

# Collections the preview page knows how to present as sections, in display
# order. Anything else in a repo renders via the generic partial or is listed
# by name only.
PREVIEW_COLLECTIONS = {
    "site.standard.document": "Writing",
    "com.whtwnd.blog.entry": "Writing",
    "sh.tangled.repo": "Projects",
    "buzz.bookhive.book": "Books",
    "social.grain.gallery": "Photos",
    "fm.teal.alpha.feed.play": "Listening",
    "app.rocksky.scrobble": "Listening",
    "community.lexicon.calendar.event": "Events",
    "my.skylights.rel": "Reviews",
    "blue.linkat.board": "Links",
}


def pages():
    """The configured lexicon pages ({slug: {collection, title}})."""
    configured = conf.as_dict().get("LEXICON_PAGES")
    return configured if configured is not None else DEFAULT_PAGES


def read_enabled():
    """Reading the repo is unauthenticated; only an identity is needed."""
    settings = conf.as_dict()
    return bool(
        settings.get("HANDLE") or (settings.get("DID") and settings.get("PDS_URL"))
    )


def _target(identity):
    """The Identity to read from (explicit one, else the site owner's).

    Returns None when unavailable — including when owner resolution fails —
    so render paths degrade instead of raising.
    """
    if identity is not None:
        return identity
    try:
        return identity_mod.owner()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"owner identity resolution failed: {e}")
        return None


def owner_identity():
    """The site owner's Identity, or None when unconfigured/unresolvable."""
    return _target(None)


def describe_repo(identity=None):
    """Collection NSIDs present in a repo, cached. Empty list on failure."""
    target = _target(identity)
    if target is None:
        return []
    cache_key = f"mosaic_atproto:collections:{target.did}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = xrpc_get(
            target.pds_url,
            "com.atproto.repo.describeRepo",
            {"repo": target.did},
            timeout=conf.get_setting("READ_TIMEOUT"),
        )
        collections = list(data.get("collections", []))
    except Exception as e:  # noqa: BLE001 - a PDS outage must not 500 the site
        logger.warning(f"describeRepo failed for {target.did}: {e}")
        return []
    cache.set(cache_key, collections, DESCRIBE_CACHE_SECONDS)
    return collections


def list_records(collection, identity=None, limit=MAX_RECORDS):
    """Records of one collection from a repo, newest first.

    Returns a list of {"uri", "cid", "rkey", "value"} dicts (rkeys are TIDs,
    so reverse listing is reverse-chronological). Cached per identity;
    empty on failure.
    """
    target = _target(identity)
    if target is None:
        return []
    cache_key = f"mosaic_atproto:records:{target.did}:{collection}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    records = []
    try:
        cursor = None
        while len(records) < limit:
            params = {
                "repo": target.did,
                "collection": collection,
                "limit": min(100, limit - len(records)),
                # rkeys are TIDs (ascending in time); reverse => newest first
                "reverse": "true",
            }
            if cursor:
                params["cursor"] = cursor
            data = xrpc_get(
                target.pds_url,
                "com.atproto.repo.listRecords",
                params,
                timeout=conf.get_setting("READ_TIMEOUT"),
            )
            for item in data.get("records", []):
                records.append(
                    {
                        "uri": item.get("uri", ""),
                        "cid": item.get("cid", ""),
                        "rkey": item.get("uri", "").rsplit("/", 1)[-1],
                        "value": item.get("value", {}),
                    }
                )
            cursor = data.get("cursor")
            if not cursor or not data.get("records"):
                break
    except Exception as e:  # noqa: BLE001 - a PDS outage must not 500 the site
        logger.warning(f"listRecords failed for {collection}: {e}")
        return []

    cache.set(cache_key, records, LIST_CACHE_SECONDS)
    return records


def blob_url(blob, identity=None):
    """Public URL for a blob dict from one of the target repo's records."""
    if not isinstance(blob, dict):
        return ""
    ref = blob.get("ref")
    cid = ref.get("$link") if isinstance(ref, dict) else ref
    if not cid:
        return ""
    target = _target(identity)
    if target is None:
        return ""
    return f"{target.pds_url}/xrpc/com.atproto.sync.getBlob?did={target.did}&cid={cid}"
