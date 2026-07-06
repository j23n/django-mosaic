"""Read-only preview: render any handle's ATmosphere content.

Powers the /@<handle> route — the "type a handle, see their home" mode. All
data is public repo data; nothing requires credentials. This is the demo
face of the aggregator and the seed of hosted multi-tenancy: the same
identity-parameterized read paths serve the owner's site and previews.
"""

import logging

from django.core.cache import cache

from . import conf, lexicons
from .client import xrpc_get

logger = logging.getLogger("django_mosaic.atproto")

APPVIEW_URL = "https://public.api.bsky.app"
PROFILE_CACHE_SECONDS = 600
RECORDS_PER_SECTION = 5


def enabled():
    return bool(conf.get_setting("PREVIEW"))


def fetch_profile(identity):
    """Bluesky profile (displayName, avatar, description); None on failure."""
    cache_key = f"mosaic_atproto:profile:{identity.did}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = xrpc_get(
            conf.as_dict().get("APPVIEW_URL", APPVIEW_URL),
            "app.bsky.actor.getProfile",
            {"actor": identity.did},
            timeout=conf.get_setting("REACTIONS_TIMEOUT"),
        )
        profile = {
            "display_name": data.get("displayName") or identity.handle,
            "description": data.get("description", ""),
            "avatar": data.get("avatar", ""),
        }
    except Exception as e:  # noqa: BLE001 - profile is decoration, degrade
        logger.warning(f"getProfile failed for {identity.did}: {e}")
        return None
    cache.set(cache_key, profile, PROFILE_CACHE_SECONDS)
    return profile


def build_sections(identity):
    """Preview sections for the collections this repo actually contains.

    Returns (sections, other_collections): sections are known collections
    with their latest records; other_collections are remaining non-Bluesky
    NSIDs listed by name so the preview shows the repo's full breadth.
    """
    present = lexicons.describe_repo(identity)
    sections = []
    seen_titles = set()
    for collection, title in lexicons.PREVIEW_COLLECTIONS.items():
        if collection not in present:
            continue
        records = lexicons.list_records(
            collection, identity=identity, limit=RECORDS_PER_SECTION
        )
        if not records:
            continue
        # Two collections can share a display title (e.g. two scrobblers);
        # keep the first with content.
        if title in seen_titles:
            continue
        seen_titles.add(title)
        sections.append(
            {
                "title": title,
                "collection": collection,
                "records": records,
                "record_template": [
                    f"lexicons/{collection}.html",
                    "lexicons/generic.html",
                ],
            }
        )
    known = set(lexicons.PREVIEW_COLLECTIONS)
    other = sorted(
        c
        for c in present
        if c not in known and not c.startswith(("app.bsky.", "chat.bsky."))
    )
    return sections, other
