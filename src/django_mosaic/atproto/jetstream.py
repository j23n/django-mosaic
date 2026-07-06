"""Jetstream consumer: live cache invalidation for repo writes.

One websocket to a Jetstream instance (`wantedDids` = the site owner plus,
when the hosted app is installed, every active tenant) turns mosaic's
read-path caches from "at most N minutes stale" into "fresh moments after
the user publishes anywhere in the ATmosphere". Purely an optimization: if
the consumer is down, pages still serve from TTL caches.

Run it as a long-lived process next to the web workers::

    python manage.py atproto jetstream

Requires the ``jetstream`` extra (``pip install django-mosaic[jetstream]``).
The event handler is synchronous and tested directly; only the reconnect
loop is async.
"""

import json
import logging

from django.core.cache import cache

from . import conf, lexicons
from . import identity as identity_mod

logger = logging.getLogger("django_mosaic.atproto")

DEFAULT_URL = "wss://jetstream2.us-east.bsky.network/subscribe"
# Persist the last seen cursor (time_us) so a restart resumes without a gap;
# Jetstream replays from a cursor up to ~72h back.
CURSOR_CACHE_KEY = "mosaic_atproto:jetstream:cursor"
CURSOR_SAVE_EVERY = 100

# The record-list limits our read paths actually cache under (see
# lexicons.list_records callers): preview sections and full lexicon pages.
_LIST_LIMITS = (5, lexicons.MAX_RECORDS)


def jetstream_url():
    return conf.get_setting("JETSTREAM_URL") or DEFAULT_URL


def wanted_dids():
    """The DIDs whose writes we care about: owner + active hosted tenants."""
    dids = []
    try:
        owner = identity_mod.owner()
        if owner:
            dids.append(owner.did)
    except Exception:  # noqa: BLE001 - unconfigured owner is fine
        pass
    try:
        from django_mosaic.hosted.models import Tenant

        dids += list(
            Tenant.objects.filter(status=Tenant.STATUS_ACTIVE).values_list(
                "did", flat=True
            )
        )
    except Exception:  # noqa: BLE001 - hosted app not installed
        pass
    # Preserve order, drop dupes; Jetstream caps wantedDids at 10 000.
    return list(dict.fromkeys(dids))[:10_000]


def handle_event(raw):
    """Process one Jetstream message; returns the event's cursor (time_us).

    Tolerant by design: unknown shapes are ignored, never raised — a bad
    event must not kill the consumer.
    """
    try:
        event = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(event, dict):
        return None
    cursor = event.get("time_us")

    did = event.get("did")
    commit = event.get("commit")
    if event.get("kind") != "commit" or not did or not isinstance(commit, dict):
        return cursor
    collection = commit.get("collection")
    if not collection:
        return cursor

    _invalidate(did, collection, commit.get("rkey"))
    return cursor


def _invalidate(did, collection, rkey=None):
    """Drop every cache a write to (did, collection) could have gone stale."""
    keys = [f"mosaic_atproto:collections:{did}"]
    keys += [
        f"mosaic_atproto:records:{did}:{collection}:{limit}" for limit in _LIST_LIMITS
    ]
    if collection == "app.bsky.actor.profile":
        keys.append(f"mosaic_atproto:profile:{did}")
    if collection == "blog.mosaic.site.settings":
        keys.append(f"mosaic_hosted:settings:{did}")
    if collection == conf.DOCUMENT_NSID and rkey:
        keys.append(f"mosaic_hosted:document:{did}:{rkey}")
    cache.delete_many(keys)
    logger.debug("jetstream: invalidated %s/%s", did, collection)


def build_url(base=None, cursor=None):
    from urllib.parse import urlencode

    params = [("wantedDids", did) for did in wanted_dids()]
    if cursor:
        params.append(("cursor", str(cursor)))
    query = urlencode(params)
    return f"{base or jetstream_url()}?{query}" if query else base or jetstream_url()


async def consume(url=None, reconnect_delay_max=60):
    """Connect and process events forever, reconnecting with backoff."""
    import asyncio

    import websockets

    delay = 1
    while True:
        cursor = cache.get(CURSOR_CACHE_KEY)
        full_url = build_url(url, cursor)
        try:
            async with websockets.connect(full_url) as socket:
                logger.info("jetstream: connected (%d dids)", len(wanted_dids()))
                delay = 1
                seen = 0
                async for message in socket:
                    event_cursor = handle_event(message)
                    seen += 1
                    if event_cursor and seen % CURSOR_SAVE_EVERY == 0:
                        cache.set(CURSOR_CACHE_KEY, event_cursor, None)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - reconnect on any failure
            logger.warning("jetstream: connection lost (%s); retrying in %ds", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, reconnect_delay_max)
