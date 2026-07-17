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
from . import preview as preview_mod

logger = logging.getLogger("django_mosaic.atproto")

DEFAULT_URL = "wss://jetstream2.us-east.bsky.network/subscribe"
# Persist the last seen cursor (time_us) so a restart resumes without a gap;
# Jetstream replays from a cursor up to ~72h back.
CURSOR_CACHE_KEY = "mosaic_atproto:jetstream:cursor"
CURSOR_SAVE_EVERY = 100

# The record-list limits our read paths actually cache under: preview
# sections and full lexicon pages. Kept in sync with the read paths by
# importing their constants rather than hardcoding the numbers.
_LIST_LIMITS = (preview_mod.RECORDS_PER_SECTION, lexicons.MAX_RECORDS)


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
    kind = event.get("kind")

    # A handle change would leave the handle→DID cache pointing at the old
    # resolution for up to an hour; drop it eagerly.
    if kind == "identity" and did:
        handle = (event.get("identity") or {}).get("handle")
        if handle:
            cache.delete(f"mosaic_atproto:identity:{handle}")
        return cursor

    commit = event.get("commit")
    if kind != "commit" or not did or not isinstance(commit, dict):
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


def build_url(dids, base=None, cursor=None):
    """The subscribe URL for an explicit DID list. Never emits an empty
    ``wantedDids`` set — that would subscribe to the entire firehose."""
    from urllib.parse import urlencode

    if not dids:
        raise ValueError("refusing to build a Jetstream URL with no wantedDids")
    params = [("wantedDids", did) for did in dids]
    if cursor:
        params.append(("cursor", str(cursor)))
    return f"{base or jetstream_url()}?{urlencode(params)}"


async def consume(url=None, reconnect_delay_max=60):
    """Connect and process events forever, reconnecting with backoff.

    ``wanted_dids`` and the cursor touch the database/cache, which is unsafe
    from an async context, so they are marshalled through ``sync_to_async``.
    They are recomputed on every (re)connect, so tenants claimed while the
    consumer is connected are picked up on the next reconnect — force a
    periodic reconnect if you need them sooner.
    """
    import asyncio

    import websockets
    from asgiref.sync import sync_to_async

    aget_dids = sync_to_async(wanted_dids)
    aget_cursor = sync_to_async(lambda: cache.get(CURSOR_CACHE_KEY))
    aset_cursor = sync_to_async(lambda v: cache.set(CURSOR_CACHE_KEY, v, None))
    # handle_event touches the cache (and, via DatabaseCache, the ORM), which is
    # unsafe from the async loop — an @async_unsafe backend raises
    # SynchronousOnlyOperation on the first event and tears the socket down.
    ahandle_event = sync_to_async(handle_event)

    delay = 1
    while True:
        dids = await aget_dids()
        if not dids:
            logger.warning("jetstream: no DIDs to watch; retrying in %ds", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, reconnect_delay_max)
            continue
        full_url = build_url(dids, url, await aget_cursor())
        try:
            async with websockets.connect(full_url) as socket:
                logger.info("jetstream: connected (%d dids)", len(dids))
                seen = 0
                async for message in socket:
                    delay = 1  # a live message proves the connection works
                    event_cursor = await ahandle_event(message)
                    seen += 1
                    if event_cursor and seen % CURSOR_SAVE_EVERY == 0:
                        await aset_cursor(event_cursor)
            # A clean close (server restart/deploy) still backs off, so we
            # never hot-loop reconnects against a bouncing endpoint.
            logger.info("jetstream: connection closed; reconnecting in %ds", delay)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - reconnect on any failure
            logger.warning("jetstream: connection lost (%s); retrying in %ds", e, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, reconnect_delay_max)
