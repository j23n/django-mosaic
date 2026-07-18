# PR 11 — Jetstream firehose consumer: live cache invalidation

> **Stack:** 11/12 · **base:** `learn/10-composer`
> **Commit:** `fc9fa7e` · **What it adds:** `manage.py atproto jetstream` (new
> optional `jetstream` extra) — one websocket to a Jetstream endpoint that
> drops the relevant read caches the *moment* a watched account writes to its
> repo, cursor-resumed across restarts.

## The one-sentence version

Every read path in mosaic caches its XRPC results on a TTL (records ~5 min,
profiles ~10 min); this PR opens **one** websocket to the ATProto firehose —
filtered down to just the DIDs you care about — and, the instant one of those
accounts commits a record, deletes exactly the caches that write could have
made stale. So "at most N minutes behind" becomes "fresh seconds after the
user publishes anywhere in the ATmosphere." It is *purely* an optimization:
turn the consumer off and pages simply fall back to their TTLs.

## Learning objectives

**ATProto**

- Understand the **firehose** (a.k.a. the event stream): the ordered,
  append-only stream of *every repo commit on a PDS/relay*, the same feed that
  powers indexers and AppViews.
- See why **Jetstream** exists: a JSON, server-filtered, lightweight
  alternative to the raw `com.atproto.sync.subscribeRepos` (which ships CBOR +
  CAR blocks + signatures). Same events, far less to parse.
- Filter at the source with **`wantedDids`** and **`wantedCollections`**, and
  understand the **cursor** (`time_us`, a microsecond timestamp) that makes the
  stream *resumable*.
- Know why an **empty `wantedDids` is dangerous** — it doesn't mean "nothing",
  it means "**the entire firehose**" (a real bug this PR still has; PR 12
  fixes it).
- Read a Jetstream **commit event**: `kind → commit → collection/rkey → the
  record that changed`, and map `(did, collection)` to "which of my caches just
  went stale."

**Python / Django**

- Write an `asyncio` **websocket consumer loop** with the `websockets` library.
- Respect the **async/sync boundary**: you *cannot* touch the Django ORM or
  cache from inside an `async def` — Django raises `SynchronousOnlyOperation`
  — so those calls must be pushed to a thread with `sync_to_async`. (This PR
  gets that boundary *wrong*; seeing the bug is the lesson.)
- Build **reconnect-with-backoff** that only resets the delay after a
  *genuinely* stable connection, and **persist a cursor** so a restart resumes
  without a gap.

## Grounding: official docs

Read these first; the consumer is a thin client over the event stream.

- Jetstream (the project + its wire format) —
  <https://github.com/bluesky-social/jetstream>
- Event stream spec (framing, cursors, the `#commit` message) —
  <https://atproto.com/specs/event-stream>
- Repository sync & `com.atproto.sync.subscribeRepos` (the raw firehose
  Jetstream wraps) — <https://atproto.com/specs/sync>,
  <https://docs.bsky.app/docs/category/http-reference>
- Django async support & the sync/async boundary —
  <https://docs.djangoproject.com/en/stable/topics/async/>
- `sync_to_async` / `async_to_sync` (the asgiref wrappers) —
  <https://github.com/django/asgiref#function-wrappers>
- The `websockets` library —
  <https://websockets.readthedocs.io/>
- Overall mental model — <https://atproto.com/guides/overview>

## Background: the model this PR implements

Everything before this PR is **pull**: a page renders, calls `list_records`,
and either serves a cached copy or makes an XRPC `GET` and caches the result
for a few minutes. Correctness is fine — the data is at most one TTL stale —
but a user who just published sees their old home page until the cache expires.

The firehose flips this to **push**. Every PDS emits an ordered stream of
**commit events**: "DID X wrote/deleted record `rkey` in collection `C`, at
sequence/time `t`." Relays aggregate these streams; indexers and AppViews
consume them to keep their views live. That stream is
`com.atproto.sync.subscribeRepos` — but it's *heavy*: binary CBOR frames
carrying CAR-encoded repo blocks and MST proofs, everything you'd need to
*cryptographically verify* the commit. Great for a relay; overkill for "tell
me when to drop a cache key."

**Jetstream** is Bluesky's answer: a service that consumes the real firehose,
strips it down to plain JSON, and lets you *filter server-side* by
`wantedDids` and/or `wantedCollections` before it ever hits the wire. You lose
the cryptographic proofs (you're trusting the Jetstream operator), which is
exactly the right trade for a cache-invalidation hint. One Jetstream message
looks roughly like:

```json
{
  "did": "did:plc:alice",
  "time_us": 1234567890123456,
  "kind": "commit",
  "commit": {
    "rev": "3k…", "operation": "create",
    "collection": "site.standard.document",
    "rkey": "3k…", "record": { … }, "cid": "bafy…"
  }
}
```

mosaic doesn't want *the whole firehose* (millions of events/sec across all of
Bluesky). It wants events for a handful of DIDs: **the site owner, plus every
active hosted tenant.** That set becomes `wantedDids`, and Jetstream sends
nothing else. When a matching `commit` arrives, mosaic reads `did` +
`collection` (+ `rkey`) and deletes the caches keyed on them. Done.

The `time_us` field is the **cursor** — a microsecond timestamp. Reconnect
with `?cursor=<time_us>` and Jetstream *replays* from there (up to ~72h back),
so a restart doesn't create a blind window. Persist the cursor and you have an
at-least-once stream across process restarts.

## Guided tour of the diff (read in this order)

### 1. `pyproject.toml` + `conf.py` — a new optional extra and one setting

The consumer needs a websocket client, and mosaic won't force it on installs
that don't run it. So a fifth extra:

```toml
jetstream = [
    "websockets>=12",
]
```

`pip install django-mosaic[jetstream]`. The endpoint is one new default in
`DEFAULTS`:

```python
"JETSTREAM_URL": "wss://jetstream2.us-east.bsky.network/subscribe",
```

(Bluesky runs a handful of public instances — `jetstream1/2` in `us-east` /
`us-west`. Any of them, or your own, works; `--url` overrides per-run.) This
is the same **inert-until-configured** discipline from PR 1: the code ships,
but nothing connects until you run the command with the extra installed.

### 2. `atproto/jetstream.py` — the whole consumer (read every line)

143 lines, four public functions and a private helper. Read them in this order.

**`wanted_dids()` — who we watch.**

```python
def wanted_dids():
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
    return list(dict.fromkeys(dids))[:10_000]
```

Three things to internalize:

- **The owner always, tenants when hosted exists.** The `Tenant` import is
  *inside* the `try` and lazy — an OSS single-site install has no `hosted` app,
  the import raises, and the consumer just watches the one owner DID. The same
  code serves both deployments.
- **Only `STATUS_ACTIVE` tenants.** Suspended tenants are excluded — you don't
  spend a firehose slot keeping a suspended site's cache warm.
- **`list(dict.fromkeys(dids))`** is the order-preserving dedupe idiom (the
  owner is frequently also tenant #1). `[:10_000]` respects Jetstream's
  documented cap on `wantedDids`.

> **Two problems are hiding here** — hold the thought for the deep dives and
> for PR 12. First, this function runs the **ORM** (`Tenant.objects.filter`),
> and it's called from inside the async `consume()` loop. Second, look at what
> `build_url` does when this returns `[]`.

**`build_url()` — the subscription URL.**

```python
params = [("wantedDids", did) for did in wanted_dids()]
if cursor:
    params.append(("cursor", str(cursor)))
query = urlencode(params)
return f"{base or jetstream_url()}?{query}" if query else base or jetstream_url()
```

`wantedDids` is a *repeated* query param, one per DID — `urlencode` on a list
of pairs produces `?wantedDids=did%3A…&wantedDids=did%3A…&cursor=123`. Now the
trap: **if `wanted_dids()` returns `[]` and there's no cursor, `query` is empty
and the URL carries no filter at all.** To Jetstream, "no `wantedDids`" means
"send me *everything*" — the full unfiltered firehose. The management command
guards against this at startup (it refuses to run with zero DIDs), but
`build_url` itself doesn't, and the guard and the URL builder can drift. PR 12
closes this hole in the function itself. This is the canonical Jetstream
footgun: **empty filter ≠ empty stream.**

**`handle_event()` — the synchronous, tolerant core.**

```python
def handle_event(raw):
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
```

Note the deliberate design: **`handle_event` is a plain synchronous function
that takes the raw string and returns a cursor.** No I/O, no websocket, no
`async`. That's *why* it's the part with seven unit tests — you can feed it a
JSON string and assert on the cache. The async loop is just a pump that hands
it messages. Separating "process one event" (pure, testable) from "keep a
socket alive" (I/O, hard to test) is the reusable move here.

It is **tolerant by contract**: bad JSON → `None`; a non-dict → `None`; a
non-`commit` kind (`identity`, `account`) → returns the cursor but touches no
cache; a commit with no collection → cursor, no-op. A malformed event must
*never* kill a long-lived consumer, so nothing in here raises.

**`_invalidate()` — drop exactly the right keys.**

```python
def _invalidate(did, collection, rkey=None):
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
```

This mirrors the *read* side exactly. `lexicons.describe_repo` caches under
`mosaic_atproto:collections:{did}`; `lexicons.list_records` caches under
`mosaic_atproto:records:{did}:{collection}:{limit}` — and the read paths only
ever call it at two limits, `5` (preview sections) and `MAX_RECORDS` (500, the
full lexicon page). Hence:

```python
_LIST_LIMITS = (5, lexicons.MAX_RECORDS)
```

The invalidation set is *derived from what the readers actually cache*. This is
the coupling to watch: add a third cached limit somewhere in the read path and
you must add it here, or that page stays stale. Collection-specific keys
(profile, hosted settings, single-document-by-rkey) are dropped only when the
matching collection is written — a document write invalidates *that document's*
cache by `rkey`, not every document.

**`consume()` — the async reconnect loop.**

```python
async def consume(url=None, reconnect_delay_max=60):
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
```

The shape is right: `async for message in socket` pumps events into the sync
handler; the cursor is reloaded before each (re)connect so a reconnect resumes;
backoff doubles to a 60 s cap; `CancelledError` is re-raised so Ctrl-C /
shutdown isn't swallowed by the catch-all. But three things are subtly off —
they're the subject of the deep dives and the reason this file gets a second
pass in PR 12. See below.

### 3. `management/commands/atproto.py` — the `jetstream` subcommand

A new `jetstream` subparser with an optional `--url`, and `_jetstream()`:

```python
def _jetstream(self, url):
    import asyncio
    try:
        import websockets  # noqa: F401
    except ImportError as exc:
        raise CommandError("… install django-mosaic[jetstream].") from exc
    from django_mosaic.atproto import jetstream

    dids = jetstream.wanted_dids()
    if not dids:
        raise CommandError("No DIDs to watch — configure MOSAIC_ATPROTO or add tenants.")
    self.stdout.write(f"Watching {len(dids)} DID(s) via {url or 'default'}...")
    try:
        asyncio.run(jetstream.consume(url))
    except KeyboardInterrupt:
        self.stdout.write("Stopped.")
```

Two guards worth naming: the **`ImportError → CommandError`** turns "extra not
installed" into a clear message instead of a traceback (the standard pattern
for optional-extra entry points), and the **empty-DIDs check** is the *startup*
line of defense against the empty-`wantedDids` footgun — but note it's checked
here, in sync land, once at boot. `asyncio.run(consume(...))` then runs the
loop until Ctrl-C.

### 4. `tests/test_atproto_jetstream.py` — testing a stream without a network

The discipline mirrors PR 1's mocked-`requests` tests: **the socket is never
opened.** Because `handle_event` is a pure sync function over a string, the
tests prime the cache, feed it a hand-built commit JSON, and assert the right
keys vanished (and *others survived* — `test_other_dids_caches_untouched`).
`WantedDidsTest` mocks `identity.owner` and creates real `Tenant` rows to prove
the dedupe + suspended-exclusion; `BuildUrlTest` asserts the URL carries the
repeated `wantedDids` and the `cursor`. The `consume()` reconnect loop is
*not* unit-tested — as the module docstring says, it was smoke-tested live.
The lesson: **carve the testable core out of the I/O shell**, and you can test
the logic deterministically while leaving only a thin async wrapper unverified.

## Deep dive: the async/sync boundary (`sync_to_async`), precisely

This is the Python heart of the PR, and this PR gets it *wrong* — which makes
it the best possible teacher.

Django's ORM and much of its machinery are **async-unsafe**. When you call the
ORM from within a running event loop, Django detects the loop and raises:

```
SynchronousOnlyOperation: You cannot call this from an async context
- use a thread or sync_to_async.
```

The guard exists because the ORM uses blocking DB drivers and thread-local
state (transactions, connections); running it directly on the event loop would
block the loop and corrupt per-thread connection state. Now look at what
`consume()` does *inside* `async def`:

- `cache.get(CURSOR_CACHE_KEY)` and `cache.set(...)` — cache access on the loop.
- `wanted_dids()` — which runs **`Tenant.objects.filter(...)`**, an ORM query,
  on the loop. Called both in `build_url(...)` and in the `logger.info` line.
- `handle_event(message)` → `_invalidate` → `cache.delete_many(...)` — cache
  writes on the loop, once per matching event.

With a database cache backend and the hosted app installed, the very first
iteration hits `SynchronousOnlyOperation` and the catch-all treats it as a
"connection lost," backs off, retries, and fails identically forever — a hot
reconnect loop that never processes an event. (With a pure in-memory cache and
no `hosted` app you might get away with it, which is exactly how a bug like
this ships past a smoke test.)

The fix is `asgiref`'s **`sync_to_async`**: it runs a synchronous callable in a
threadpool and `await`s the result, so the blocking ORM/cache work happens
*off* the event loop on a real thread where Django is happy:

```python
from asgiref.sync import sync_to_async

# inside consume():
cursor = await sync_to_async(cache.get)(CURSOR_CACHE_KEY)
dids = await sync_to_async(wanted_dids)()
full_url = build_url_from(dids, cursor)          # pure, no I/O
...
await sync_to_async(handle_event)(message)       # ORM/cache now on a thread
```

The rule to carry forward: **inside `async def`, every ORM or cache call must
cross a `sync_to_async` boundary.** The mirror image is `async_to_sync`, for
calling async code from sync (Django exposes both as e.g.
`Model.objects.aget`). `handle_event` and `_invalidate` were deliberately left
synchronous *precisely so* they can be wrapped in one `sync_to_async` at the
boundary rather than being sprinkled with `await` — good separation, but this
PR forgot to actually place the wrapper. **PR 12 adds the `sync_to_async`
wrappers.** When you review PR 12, this is one of the two changes to look for
in this file.

## Deep dive: robust reconnect and cursor-resume

A firehose consumer runs for weeks. Connections drop — deploys, PDS restarts,
network blips — so "reconnect" isn't an edge case, it's the steady state. Two
things have to be right.

**Backoff that only resets after a *stable* connection.** The intent of
`delay = 1` on connect is "we're healthy again, forget the backoff." But it
resets *the instant the socket opens*, before a single message is read. If
Jetstream accepts the TCP/WS handshake and then immediately closes (bad cursor,
overload, auth hiccup), you connect → reset delay to 1 → drop → sleep 1s →
connect → reset → drop… a tight 1-second reconnect storm that never backs off,
because "opened a socket" was mistaken for "connection is stable." The robust
version resets the delay only *after evidence of a working stream* — e.g. after
the first successfully handled message, or after N seconds of uptime — so a
flapping endpoint actually rides the exponential curve up to the 60 s cap.

**Cursor persistence you don't lose on the way down.** The cursor is what makes
restarts gap-free, and it's saved with `cache.set(CURSOR_CACHE_KEY, ..., None)`
(no expiry) — good. But the throttle is **count-based**: `seen % 100 == 0`.
Consider a low-traffic watch set (one owner, a few tenants) that emits, say, 40
commits and then the process is killed. `seen` never reached 100, the cursor
was *never persisted*, and on restart you replay from whatever was last saved —
possibly hours ago, or nothing. Count-based throttling ties your durability to
traffic volume, which is backwards: the *quieter* the stream, the *longer* you
go without checkpointing. The fix is a **time-based flush** — persist the
cursor at most every ~5 seconds regardless of event count — so your worst-case
replay window is bounded by wall-clock, not by how chatty the watched repos
happen to be. **PR 12 makes the cursor flush time-based.** That's the second
change to look for in this file next lesson.

Take the two deep dives together and you have PR 12's punch list for
`jetstream.py`: **guard empty `wantedDids`, cross the `sync_to_async` boundary,
and flush the cursor on time, not on count.** This PR is deliberately the
"it works on my machine" version so you can *find* those three before you read
the fix.

## Design decisions & "why not X"

- **Why Jetstream, not raw `subscribeRepos`?** The raw firehose is CBOR + CAR
  blocks + signatures — everything needed to *verify* a commit. mosaic doesn't
  verify anything; it just wants a "drop this cache" nudge. Jetstream's
  pre-filtered JSON is a fraction of the bytes and code, and server-side
  `wantedDids` means you never even receive the millions of events you'd throw
  away. The cost — trusting the Jetstream operator's honesty — is irrelevant
  for a hint whose failure mode is "a page is briefly stale."
- **Why one websocket for all tenants, not one per tenant?** Jetstream filters
  a *list* of DIDs on a single subscription (up to 10 000). One socket, one
  process, one cursor — vs. thousands of sockets. When you outgrow 10k you
  *shard* consumers across DID ranges, which is why the doc note mentions the
  cap explicitly.
- **Why is this "purely an optimization"?** Because correctness already lives
  in the TTL caches from PRs 2/5/10. If the consumer is down, misconfigured, or
  eating a `SynchronousOnlyOperation`, every page still renders — just up to one
  TTL stale. That's what lets this ship as a *separate process with a separate
  extra*: it can fail without taking the site with it. Never let an
  optimization become a correctness dependency.
- **Why persist the cursor to the cache, not the DB?** The cache is already a
  hard dependency of every read path, the value is a single disposable integer,
  and losing it costs you at most a ~72h replay on next connect. A migration and
  a table would be over-engineering for a resumable-from-anywhere pointer.

## Exercises

1. **Trace an invalidation.** Owner `did:plc:alice` publishes a post →
   `site.standard.document` commit with `rkey=3k…`. List every cache key
   `_invalidate` deletes. Which one is `rkey`-specific, and which page would
   stay stale if the read path started caching `list_records` at `limit=20`?
2. **Spot the footgun.** Construct the exact conditions under which
   `build_url()` returns a URL with **no** `wantedDids`. What does Jetstream do
   with that subscription, and why is the `_jetstream()` startup check *not*
   enough to prevent it in general? (This is PR 12's empty-`wantedDids` guard.)
3. **Predict the crash.** With `CACHES` set to Django's database backend and
   the `hosted` app installed, walk `consume()` line by line from entry. At
   which call does `SynchronousOnlyOperation` fire, and why does the catch-all
   turn it into an infinite reconnect loop rather than a visible error?
4. **Fix the boundary.** Rewrite the body of `consume()` so every ORM/cache
   touch crosses `sync_to_async`, keeping `handle_event` synchronous. Where is
   the single cleanest place to wrap `wanted_dids()` so it isn't run twice per
   connect?
5. **Bound the replay window.** Replace the `seen % 100` cursor flush with a
   time-based one (persist at most every 5 s). On a stream that emits 3 events
   then dies, how far back does your version replay vs. the original?

## Verify it yourself

```bash
git checkout learn/11-jetstream
python -m pytest tests/test_atproto_jetstream.py -q     # socket never opened
git show fc9fa7e -- src/django_mosaic/atproto/jetstream.py   # the whole consumer

# Live (needs the extra + network): watch your own repo update a cache.
pip install -e '.[jetstream]'
python manage.py atproto jetstream --url wss://jetstream1.us-east.bsky.network/subscribe
```

Then, in another shell, publish a post (`manage.py atproto publish` or the
composer) and watch the consumer log `jetstream: invalidated <did>/<nsid>` —
and reload your home page to see it fresh before the TTL would have expired.

## Glossary

- **Firehose / event stream** — the ordered, append-only stream of every repo
  commit; `com.atproto.sync.subscribeRepos` is its canonical (CBOR) form.
- **Relay** — a service that aggregates many PDSes' firehoses into one stream.
- **Jetstream** — a Bluesky service that converts the firehose to filtered JSON
  (`wantedDids` / `wantedCollections`), trading cryptographic proofs for size.
- **`wantedDids` / `wantedCollections`** — server-side subscription filters;
  an **empty** filter means *everything*, not nothing.
- **Cursor (`time_us`)** — a microsecond timestamp marking your position in the
  stream; reconnect with it to replay (Jetstream keeps ~72h).
- **Commit event** — `{kind:"commit", did, commit:{collection, rkey, operation,
  record, cid}}`; the shape mosaic maps to cache keys.
- **`sync_to_async` / `async_to_sync`** — asgiref wrappers that run sync code in
  a thread from async (and vice-versa); the ORM/cache require the former inside
  `async def`.
- **`SynchronousOnlyOperation`** — Django's error when async-unsafe code (ORM,
  cache) is called directly on the event loop.
- **Backoff** — exponential reconnect delay; must reset only after a *stable*
  connection, not merely an opened socket.
