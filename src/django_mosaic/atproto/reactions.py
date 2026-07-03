"""Fetch reactions to a post from the ATmosphere.

Two sources, both public/unauthenticated and cached:

- The Bluesky AppView (`app.bsky.feed.getPostThread`) for like/repost counts
  and the reply thread of the companion post — the "comment section".
- Constellation (backlink index, constellation.microcosm.blue) for cross-app
  reactions: standard.site recommends, Leaflet comments, Tangled stars, or
  any other lexicon linking to the document's AT-URI or canonical URL.

Every fetch degrades gracefully: on any error the section simply renders
without that data. A third-party index being down must never 500 a post page.
"""

import logging

import requests

from django.core.cache import cache

from . import conf
from .client import xrpc_get

logger = logging.getLogger("django_mosaic.atproto")

APPVIEW_URL = "https://public.api.bsky.app"
CONSTELLATION_URL = "https://constellation.microcosm.blue"

THREAD_CACHE_SECONDS = 300
CONSTELLATION_CACHE_SECONDS = 600

# Known reaction sources: (collection, human label). Anything else that links
# to the post is summarized under its collection NSID.
KNOWN_SOURCES = {
    "app.bsky.feed.like": "Bluesky likes",
    "app.bsky.feed.repost": "Bluesky reposts",
    "app.bsky.feed.post": "Bluesky mentions",
    "site.standard.graph.recommend": "recommends",
    "pub.leaflet.comment": "Leaflet comments",
    "com.whtwnd.blog.comment": "WhiteWind comments",
    "sh.tangled.feed.star": "Tangled stars",
    "social.grain.favorite": "Grain favorites",
}


def _appview_url():
    return conf.as_dict().get("APPVIEW_URL", APPVIEW_URL)


def _constellation_url():
    return conf.as_dict().get("CONSTELLATION_URL", CONSTELLATION_URL)


def bsky_web_url(at_uri):
    """https://bsky.app permalink for an app.bsky.feed.post AT-URI."""
    try:
        _, _, did, _, rkey = at_uri.split("/")
        return f"https://bsky.app/profile/{did}/post/{rkey}"
    except ValueError:
        return ""


def _flatten_replies(node, depth=0, max_depth=6):
    """Flatten a getPostThread reply tree into template-friendly dicts."""
    replies = []
    for child in node.get("replies") or []:
        post = child.get("post") or {}
        author = post.get("author") or {}
        record = post.get("record") or {}
        if post.get("uri"):
            replies.append(
                {
                    "uri": post["uri"],
                    "web_url": bsky_web_url(post["uri"]),
                    "handle": author.get("handle", ""),
                    "display_name": author.get("displayName")
                    or author.get("handle", ""),
                    "avatar": author.get("avatar", ""),
                    "text": record.get("text", ""),
                    "created_at": record.get("createdAt", ""),
                    "depth": depth,
                }
            )
        if depth < max_depth:
            replies.extend(_flatten_replies(child, depth + 1, max_depth))
    return replies


def fetch_thread(bsky_post_uri):
    """Counts + flattened replies for the companion post; None on failure."""
    if not bsky_post_uri:
        return None
    cache_key = f"mosaic_atproto:thread:{bsky_post_uri}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = xrpc_get(
            _appview_url(),
            "app.bsky.feed.getPostThread",
            {"uri": bsky_post_uri, "depth": 6},
        )
        thread = data.get("thread") or {}
        post = thread.get("post") or {}
        result = {
            "uri": bsky_post_uri,
            "web_url": bsky_web_url(bsky_post_uri),
            "like_count": post.get("likeCount", 0),
            "repost_count": post.get("repostCount", 0),
            "reply_count": post.get("replyCount", 0),
            "replies": _flatten_replies(thread),
        }
    except Exception as e:  # noqa: BLE001 - degrade, never 500 the page
        logger.warning(f"getPostThread failed for {bsky_post_uri}: {e}")
        return None
    cache.set(cache_key, result, THREAD_CACHE_SECONDS)
    return result


def _parse_constellation(data):
    """Tolerantly parse /links/all output into {collection: count}.

    Constellation groups backlinks as {collection: {json_path: count}}; the
    envelope key has varied ("links"), so accept both wrapped and flat forms
    and count values that are either ints or lists.
    """
    counts = {}
    if not isinstance(data, dict):
        return counts
    links = data.get("links", data)
    if not isinstance(links, dict):
        return counts
    for collection, paths in links.items():
        if not isinstance(paths, dict):
            continue
        total = 0
        for value in paths.values():
            if isinstance(value, int):
                total += value
            elif isinstance(value, list):
                total += len(value)
            elif isinstance(value, dict):
                # e.g. {"records": N, "distinct_dids": M}
                inner = value.get("records")
                if isinstance(inner, int):
                    total += inner
        if total:
            counts[collection] = counts.get(collection, 0) + total
    return counts


def fetch_crossapp_counts(targets):
    """Backlink counts for the given targets (AT-URIs and/or URLs), merged.

    Returns a list of {"collection", "label", "count"} sorted by count.
    """
    merged = {}
    for target in [t for t in targets if t]:
        cache_key = f"mosaic_atproto:constellation:{target}"
        counts = cache.get(cache_key)
        if counts is None:
            try:
                resp = requests.get(
                    f"{_constellation_url()}/links/all",
                    params={"target": target},
                    timeout=conf.get_setting("TIMEOUT"),
                )
                resp.raise_for_status()
                counts = _parse_constellation(resp.json())
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Constellation lookup failed for {target}: {e}")
                counts = {}
            cache.set(cache_key, counts, CONSTELLATION_CACHE_SECONDS)
        for collection, count in counts.items():
            merged[collection] = merged.get(collection, 0) + count

    # The companion post's own likes/reposts are already shown via the
    # AppView; drop them here to avoid double counting.
    merged.pop("app.bsky.feed.like", None)
    merged.pop("app.bsky.feed.repost", None)

    return sorted(
        (
            {
                "collection": collection,
                "label": KNOWN_SOURCES.get(collection, collection),
                "count": count,
            }
            for collection, count in merged.items()
        ),
        key=lambda item: -item["count"],
    )


def reactions_for(post):
    """Everything the reactions template needs, for a synced post."""
    document = getattr(post, "atproto_document", None)
    if document is None:
        return None
    thread = fetch_thread(document.bsky_post_uri)
    crossapp = fetch_crossapp_counts(
        [document.uri, f"{conf.publication_url()}{post.get_absolute_url()}"]
    )
    return {"document": document, "thread": thread, "crossapp": crossapp}
