"""Read arbitrary lexicon collections from the owner's repo and render them.

This is the "personal AppView" half of the bridge: any collection in your PDS
repo (Tangled repos, BookHive books, ...) can be listed on the site with two
XRPC reads and a template. No models, no migrations — records are rendered
straight from their JSON via a template-per-NSID registry:

    templates/lexicons/<collection NSID>.html   (record partial; falls back
    templates/lexicons/generic.html              to a raw-ish dump)

Pages are configured in MOSAIC_ATPROTO["LEXICON_PAGES"]; consumers customize
a page by overriding its partial (or `lexicon-page.html`) in their project's
template directory — the same override mechanism as the rest of mosaic.
"""

import logging

from django.core.cache import cache

from . import conf
from .client import resolve_identity, xrpc_get

logger = logging.getLogger("django_mosaic.atproto")

LIST_CACHE_SECONDS = 300
IDENTITY_CACHE_SECONDS = 3600
MAX_RECORDS = 500

DEFAULT_PAGES = {
    "projects": {"collection": "sh.tangled.repo", "title": "Projects"},
    "books": {"collection": "buzz.bookhive.book", "title": "Books"},
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


def identity():
    """(did, pds_url) for the site owner, cached."""
    cache_key = "mosaic_atproto:identity"
    cached = cache.get(cache_key)
    if cached:
        return cached
    resolved = resolve_identity(conf.get_setting("HANDLE"))
    cache.set(cache_key, resolved, IDENTITY_CACHE_SECONDS)
    return resolved


def list_records(collection, limit=MAX_RECORDS):
    """All records of one collection from the owner's repo, newest first.

    Returns a list of {"uri", "cid", "rkey", "value"} dicts (rkeys are TIDs,
    so reverse listing is reverse-chronological). Cached; empty on failure.
    """
    cache_key = f"mosaic_atproto:records:{collection}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    records = []
    try:
        did, pds_url = identity()
        cursor = None
        while len(records) < limit:
            params = {
                "repo": did,
                "collection": collection,
                "limit": min(100, limit - len(records)),
                # rkeys are TIDs (ascending in time); reverse => newest first
                "reverse": "true",
            }
            if cursor:
                params["cursor"] = cursor
            data = xrpc_get(pds_url, "com.atproto.repo.listRecords", params)
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


def blob_url(blob):
    """Public URL for a blob dict from one of the owner's records."""
    if not isinstance(blob, dict):
        return ""
    ref = blob.get("ref")
    cid = ref.get("$link") if isinstance(ref, dict) else ref
    if not cid:
        return ""
    try:
        did, pds_url = identity()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"identity resolution failed for blob URL: {e}")
        return ""
    return f"{pds_url}/xrpc/com.atproto.sync.getBlob?did={did}&cid={cid}"
