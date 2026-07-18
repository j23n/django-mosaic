# PR 2 — Reactions & comments (`django_mosaic.atproto.reactions`)

> **Stack:** 2/12 · **base:** `learn/01-atproto-bridge`
> **Commit:** `368ac32` · **What it adds:** a read-only reactions/comments
> section on synced posts, assembled from the Bluesky AppView *and* the
> Constellation backlink index, cached and degrading to nothing on failure.

## The one-sentence version

PR 1 wrote your post *out* to the ATmosphere; this PR reads the ATmosphere's
response *back in* — like/repost counts and the reply thread of your companion
Bluesky post (that's your comment section) via `getPostThread`, plus a tally of
everyone else who linked to the post's AT-URI or URL (recommends, cross-app
comments, stars, favorites) via Constellation — both cached, both fail-soft, and
rendered by a `{% atproto_reactions %}` tag that appears only when the post has a
synced document.

## Learning objectives

**ATProto**

- Know what `app.bsky.feed.getPostThread` returns and *why the companion
  `app.bsky.feed.post` IS the comment thread* for your article.
- Distinguish an **AppView** ("one app's materialized view of the network") from
  a **backlink index** ("who, in any app, links to this URI/URL") — the
  difference between Bluesky's `getPostThread` and Constellation.
- Understand **AT-URI backlinks vs canonical-URL backlinks**: the same reaction
  can point at your `site.standard.document` record *or* at your `https://…`
  page, and you have to look up both.
- See how counts **double-count** across sources and the one-line rule this PR
  uses to avoid it.

**Python / Django**

- **Tiered caching** — two sources, two TTLs (5 min for the thread, 10 min for
  the index), keyed per target.
- Writing a **tolerant parser** for a third-party JSON contract: accept
  wrapped-or-flat envelopes and int-or-list-or-dict count shapes without ever
  raising.
- **Graceful degradation** — an upstream outage must render the page *without*
  the section, never 500 it.
- A **custom `simple_tag`** that conditionally returns `None`, plus the template
  guard that keeps the whole feature invisible on sites without the bridge.

## Grounding: official docs

Read these first; `reactions.py` is a thin, defensive client over them.

- `app.bsky.feed.getPostThread` (counts + reply tree) —
  <https://docs.bsky.app/docs/api/app-bsky-feed-get-post-thread>
- The bsky HTTP/XRPC reference (where the AppView methods live) —
  <https://docs.bsky.app/docs/category/http-reference>
- `app.bsky.feed.post` + embeds (what the companion post *is*) —
  <https://docs.bsky.app/docs/advanced-guides/posts>
- AT-URI scheme (one of the two backlink targets) —
  <https://atproto.com/specs/at-uri-scheme>
- Constellation backlink index — <https://constellation.microcosm.blue> ;
  source & API shape — <https://github.com/at-microcosm/links>
- `site.standard.*` (the document whose URI we look up) —
  <https://standard.site/>
- Protocol overview / mental model — <https://atproto.com/guides/overview>
- Django's cache framework — <https://docs.djangoproject.com/en/stable/topics/cache/>

> **A caveat, same as PR 1.** The `getPostThread` and Constellation parsers here
> were written against the *documented* response shapes, and the sandbox proxy
> blocks both `public.api.bsky.app` and `constellation.microcosm.blue`. The
> defensive parsing (below) exists precisely because the live shapes have not
> been round-tripped here — treat the exact JSON paths as "verify against the
> live API before you trust the numbers," which is what the commit message says
> too.

## Background: two very different ways to ask "who reacted?"

Your article now exists as **two records** in the ATmosphere (PR 1): a
`site.standard.document` (the app-neutral article) and a companion
`app.bsky.feed.post` (the social artifact), cross-linked by `bskyPostRef`.
Reactions can therefore accrue in two fundamentally different places, and this
PR reads *both*.

**1. The AppView — Bluesky's view of the Bluesky post.** An **AppView** is a
service that ingests the firehose for one application and materializes it into
queryable views: threads, feeds, like counts. `public.api.bsky.app` is
Bluesky's public, unauthenticated AppView. When you ask it
`getPostThread(uri=<your companion post>)`, it hands back that post *with its
aggregated `likeCount` / `repostCount` / `replyCount`* and a **tree of
replies** — each reply being another `app.bsky.feed.post`. The insight worth
sitting with: **a reply to your companion post is exactly a blog comment.**
Someone opens your article in Bluesky, hits reply, types a paragraph — that
paragraph is a first-class record in *their* repo, and `getPostThread` stitches
it into the thread. mosaic doesn't run a comment system; it borrows Bluesky's.

**2. A backlink index — anyone, in any app, who pointed at your post.** The
AppView only knows about `app.bsky.*`. But your document has an AT-URI and your
page has a URL, and records in *other* lexicons can link to either:
`site.standard.graph.recommend`, `pub.leaflet.comment`, `com.whtwnd.blog.comment`,
`sh.tangled.feed.star`, `social.grain.favorite`, or something that didn't exist
when this code was written. No single AppView indexes all of those. A **backlink
index** does: **Constellation** (`constellation.microcosm.blue`, from the
`at-microcosm/links` project) ingests the whole network and answers the inverse
question — *"give me every record whose contents point at this target."* You
hand it a target (an AT-URI *or* a URL) and it returns backlinks grouped by the
linking collection. That's how a "recommend" from a standard.site reader and a
"star" from Tangled show up next to your Bluesky likes.

The mental model to lock in:

```
AppView   (app.bsky.feed.getPostThread)  →  "Bluesky's view of ONE post"
                                             counts + reply tree, one app
backlink index (Constellation /links/all) →  "who links to this target, ANYWHERE"
                                             grouped by collection, all apps
```

They overlap — Constellation *also* sees `app.bsky.feed.like` records — which is
the source of the double-counting problem this PR has to solve.

## Guided tour of the diff (read in this order)

### 1. `atproto/reactions.py` — the module docstring and constants

Start at the top. The docstring states the whole design in five lines: two
sources, both public/unauthenticated, both cached, both fail-soft. Then the
knobs:

```python
APPVIEW_URL = "https://public.api.bsky.app"
CONSTELLATION_URL = "https://constellation.microcosm.blue"
THREAD_CACHE_SECONDS = 300          # 5 min
CONSTELLATION_CACHE_SECONDS = 600   # 10 min
```

`KNOWN_SOURCES` maps linking-collection NSIDs to human labels
(`site.standard.graph.recommend → "recommends"`). Anything not in the map falls
back to its raw NSID — the feature is **forward-compatible with lexicons that
don't exist yet**. A brand-new app that starts linking to your posts shows up as
a count under its own NSID with zero code changes. That's the payoff of building
on an *index* instead of a fixed list of integrations.

`_appview_url()` / `_constellation_url()` read `conf.as_dict()` so both hosts are
overridable (self-hosted AppView, a different index) — same "nothing forces you
through a central service" theme from PR 1.

### 2. `bsky_web_url()` — parsing an AT-URI by hand

```python
def bsky_web_url(at_uri):
    try:
        _, _, did, _, rkey = at_uri.split("/")
        return f"https://bsky.app/profile/{did}/post/{rkey}"
    except ValueError:
        return ""
```

`at://did/collection/rkey` split on `/` yields
`["at:", "", did, collection, rkey]` — five parts (the `//` gives an empty
element). This turns an AT-URI into a human bsky.app permalink. Note the failure
mode: a malformed URI returns `""`, and the template simply omits the link. This
is the module's whole philosophy in miniature — **bad input degrades to
absence, never to an exception.** (An AT-URI can technically carry a query/
fragment per the spec; this splitter assumes the plain three-segment form the
AppView emits. Fine here, worth knowing.)

### 3. `_flatten_replies()` — the thread tree → a flat comment list

`getPostThread` returns a *nested* tree (`replies` inside `replies`). Templates
render flat lists far more happily than recursion, so this walks the tree
depth-first and emits one dict per reply, carrying a `depth` used later for
indentation:

- It reads each node defensively — `post.get("author") or {}`,
  `post.get("record") or {}` — because any node might be a blocked/deleted
  placeholder (`app.bsky.feed.defs#notFoundPost`) with no `post`.
- It only appends replies that actually have a `uri`.
- `max_depth=6` bounds the recursion (matching the `depth: 6` that `fetch_thread`
  asks the API for), so a pathological thread can't blow the stack or the page.

### 4. `fetch_thread()` — the cached AppView call

The shape to memorize (you'll write it a hundred times):

```python
cached = cache.get(cache_key)
if cached is not None:      # cache hit — including an empty/None-ish result
    return cached
try:
    ... expensive call ...
except Exception:           # degrade
    return None             # NB: not cached
cache.set(cache_key, result, THREAD_CACHE_SECONDS)
return result
```

It pulls `likeCount` / `repostCount` / `replyCount` off `thread.post` and the
flattened `replies` off `thread`. Key detail: **failures return `None` and are
*not* cached**, so a transient AppView blip is retried on the next request;
successes are cached for 5 minutes. (Contrast with Constellation below, which
*does* cache its empty result — a deliberate difference, see the deep dive.)

### 5. `_parse_constellation()` — the tolerant parser (the crux)

This is the most instructive function in the PR; the deep dive dissects it. For
the tour: it takes Constellation's JSON and reduces it to `{collection: count}`,
tolerating (a) a `{"links": {...}}` wrapper *or* a flat top-level dict, and
(b) per-path values that are an `int`, a `list` (count its length), or a `dict`
(read `.records`). Anything it doesn't understand it silently skips. It cannot
raise on shape — only a non-dict top level short-circuits to `{}`.

### 6. `fetch_crossapp_counts()` — per-target caching + the dedupe rule

```python
for target in [t for t in targets if t]:
    cache_key = f"mosaic_atproto:constellation:{target}"
    counts = cache.get(cache_key)
    if counts is None:
        ... requests.get(.../links/all?target=<target>) ...
        cache.set(cache_key, counts, CONSTELLATION_CACHE_SECONDS)
    for collection, count in counts.items():
        merged[collection] = merged.get(collection, 0) + count

merged.pop("app.bsky.feed.like", None)
merged.pop("app.bsky.feed.repost", None)
```

Two things happen here. First, **each target is cached independently** — the
AT-URI and the canonical URL are separate lookups with separate cache entries,
then merged. Second, the **anti-double-count rule**: Constellation sees Bluesky
likes/reposts too, and those are *already* shown from the AppView thread, so
they're popped before returning. Drop that `.pop()` and every Bluesky like is
counted twice. The result is sorted by descending count for display.

### 7. `reactions_for()` — the one public entry point

```python
document = getattr(post, "atproto_document", None)
if document is None:
    return None
thread = fetch_thread(document.bsky_post_uri)
crossapp = fetch_crossapp_counts(
    [document.uri, f"{conf.publication_url()}{post.get_absolute_url()}"]
)
return {"document": document, "thread": thread, "crossapp": crossapp}
```

`atproto_document` is the reverse accessor of the `DocumentRecord` one-to-one
from PR 1. **No synced document → return `None` → the section never renders.**
The two crossapp targets are exactly the AT-URI and the canonical URL — the two
things anyone in the network might have linked to.

### 8. `templatetags/mosaic_atproto.py` — the conditional tag

```python
@register.simple_tag
def atproto_reactions(post):
    return reactions.reactions_for(post)
```

A `simple_tag` that returns a dict or `None`. The `{% ... as reactions %}` form
binds the result, and the template's `{% if reactions %}` does the rest — the
tag *conditionally renders* by returning a falsy value.

### 9. `templates/atproto/reactions.html` — the partial

Reads top-down: a counts row (`♥ likeCount`, `🔁 repostCount`, then each crossapp
`count label`), a "Reply on Bluesky" link, and the `h-feed` comment section.
Each reply is an `h-entry` `article` indented by `margin-left: {{ depth }}em`.
Two things to notice:

- **`{{ reply.text }}` is auto-escaped.** The comment came from a stranger's
  Bluesky post; Django's autoescape turns `<script>` into `&lt;script&gt;`. The
  test asserts this explicitly. Never mark comment text safe.
- **Microformats2 classes** (`h-feed`, `h-entry`, `p-author`, `e-content`,
  `dt-published`) make the comments machine-readable to *other* consumers — the
  same "write to the format the ecosystem understands" instinct as the two-record
  design in PR 1.

There's an `{% elif reactions.crossapp %}` branch: if the AppView thread failed
(`thread is None`) but Constellation succeeded, you still get the crossapp counts
row. Partial data beats no data.

### 10. `templates/post-detail.html` — the guard

```django
{% if post.atproto_document %}{% include "atproto/reactions.html" %}{% endif %}
```

Belt *and* braces: the include is guarded by `post.atproto_document`, and
`reactions_for` re-checks the same thing. A mosaic site **without** the bridge
installed has no `atproto_document` accessor relation populated, so the whole
feature stays invisible and untouched — exactly the "optional sub-app" contract
from PR 1.

## Deep dive: tolerant parsing + tiered caching

### Defensive parsing of a third-party contract

You're consuming JSON from a young, independent project (Constellation) whose
envelope has already shifted once. You have three bad options — assume one shape
and `KeyError` in production, pin to a version that doesn't exist, or *parse
tolerantly*. `_parse_constellation` takes the third:

```python
def _parse_constellation(data):
    counts = {}
    if not isinstance(data, dict):
        return counts                 # not even a dict → give up quietly
    links = data.get("links", data)   # wrapped {"links": …} OR flat: same code
    if not isinstance(links, dict):
        return counts
    for collection, paths in links.items():
        if not isinstance(paths, dict):
            continue                  # skip anything unexpected, don't raise
        total = 0
        for value in paths.values():
            if isinstance(value, int):
                total += value        # {".path": 7}  → 7
            elif isinstance(value, list):
                total += len(value)   # {".path": ["a","b"]} → 2
            elif isinstance(value, dict):
                inner = value.get("records")   # {".path": {"records": N}} → N
                if isinstance(inner, int):
                    total += inner
        if total:
            counts[collection] = counts.get(collection, 0) + total
    return counts
```

The principles generalize to *any* untrusted JSON boundary:

1. **Type-check before you traverse.** Every `.get`/iteration is preceded by an
   `isinstance`. The parser has no line that can raise on a wrong shape — the
   worst input yields `{}`, not a 500.
2. **Collapse equivalent shapes at the boundary.** `data.get("links", data)`
   handles wrapped and flat with one expression; the int/list/dict branches
   normalize three count encodings to one integer. The rest of the program sees
   a single clean shape (`{collection: int}`) and never learns the wire was
   messy.
3. **Prefer skipping to guessing.** Unknown value types contribute nothing
   rather than a fabricated count. An undercount is a smaller lie than a made-up
   number.
4. **`if total:`** — collections with zero backlinks are dropped, so the UI
   never shows "0 recommends."

This is the same defensiveness as `_flatten_replies`' `... or {}` and
`bsky_web_url`'s `except ValueError`. Across the module, *malformed upstream data
becomes missing UI, never an exception.*

### Tiered caching — two TTLs, per-target keys

Django's cache framework (`from django.core.cache import cache`) is a
process-global key/value store; `cache.set(key, value, seconds)` gives each
entry its own TTL. This PR uses **three deliberate caching choices**:

- **Different TTLs per source.** The thread is cached 5 min (`THREAD_CACHE_SECONDS`)
  and Constellation 10 min (`CONSTELLATION_CACHE_SECONDS`). Bluesky reply threads
  move faster than cross-app backlinks and the AppView is cheap and reliable; the
  index is slower-moving and the query is heavier, so it's cached longer. TTL is a
  per-source freshness-vs-load dial, not one global number.
- **Per-target keys.** `mosaic_atproto:constellation:{target}` means the AT-URI
  lookup and the URL lookup are cached independently and reused across posts that
  share a target. The namespaced prefix keeps mosaic's keys from colliding with
  the host project's.
- **Cache the empty result (Constellation) but not the failure (thread).**
  `fetch_crossapp_counts` caches `{}` on error, so a downed index isn't hammered
  once per pageview for 10 minutes — the outage itself is cached. `fetch_thread`
  returns `None` *without* caching, so the AppView is retried next request. Two
  reasonable policies; the choice reflects "how expensive is a miss vs. how much
  do I want fast recovery." Worth asking which you'd pick and why (see exercises).

### Graceful degradation, end to end

Trace a total ATmosphere outage through the stack: `xrpc_get` raises →
`fetch_thread` catches, logs a warning, returns `None`. `requests.get` raises →
`fetch_crossapp_counts` catches per-target, returns `{}`, yielding `[]`.
`reactions_for` returns `{"thread": None, "crossapp": []}` — truthy, so the
`<section>` still opens, but every inner `{% if %}` is false, so it renders
empty. The `test_page_survives_all_sources_down` test asserts a plain `200`.
**No external dependency in this feature can take down a post page** — the catch
is at every network boundary and the template tolerates every-field-missing.

## Design decisions & "why not X"

- **Why two sources instead of just Constellation?** Constellation *does* index
  `app.bsky.feed.like/repost/post`, so in principle it could report Bluesky
  numbers too. But the AppView gives you the *reply text and authors* (the actual
  comment section) and authoritative aggregate counts in one call; the index
  gives you *reach across apps*. You want both, and you want the counts from the
  authoritative source — hence pull Bluesky counts from the AppView and *drop*
  them from Constellation.
- **Why look up both the AT-URI and the URL?** Different apps link differently.
  A standard.site recommend targets the document's **AT-URI**; a WhiteWind or
  Leaflet comment, or a plain web mention, may target the **canonical URL**.
  Querying only one misses half the reactions. Merging both (and de-duping
  Bluesky) is the price of a complete picture.
- **Why cache the *failure* for Constellation but retry the thread?**
  Constellation is a single third-party index with a heavier query; caching `{}`
  shields it (and your latency) during an outage. The AppView is cheap, fast, and
  first-party-ish; retrying quickly costs little and recovers instantly. The
  asymmetry is intentional, not an oversight.
- **Why a `simple_tag` returning `None` instead of a context processor or a
  view-layer fetch?** The fetch is *lazy and local to the template* that needs
  it — no per-request cost on pages that don't render reactions, and the
  cache does the heavy lifting on the pages that do. It keeps the whole feature
  inside the sub-app: one tag, one partial, one `{% if %}` in the host template.
- **Why not run a real comment system?** Because the ATmosphere already is one.
  A reply to your companion post is a durable record in the replier's own repo,
  moderated by Bluesky, portable, and yours to read for free. Building a
  comment DB would be re-implementing infrastructure you get by writing one
  `app.bsky.feed.post`.

## Exercises

1. **Predict the bug (double counting).** Delete the two `merged.pop(...)` lines
   in `fetch_crossapp_counts`. What does the counts row show for a post with 7
   Bluesky likes? *Answer:* `♥ 7` from the AppView **and** `7 Bluesky likes` from
   Constellation — the same likes reported twice. The `.pop()` is the dedupe.

2. **Predict the bug (cache poisoning on error).** Suppose you "improve"
   `fetch_thread` to `cache.set(cache_key, None, ...)` inside the `except`. What
   breaks? *Answer:* a single transient AppView error freezes the comment section
   to empty for the full 5-minute TTL, because the next request gets the cached
   `None` and never retries. Not caching failures is why the thread recovers on
   the very next pageview.

3. **Read the shape.** Given
   `{"links": {"sh.tangled.feed.star": {".subject": ["a", "b", "c"]}}}`, hand-run
   `_parse_constellation`. *Answer:* `{"sh.tangled.feed.star": 3}` — the list
   branch counts length. This is exactly `test_unwrapped_and_list_shapes_tolerated`
   minus the wrapper.

4. **Hands-on (XSS check).** A Bluesky reply contains
   `Nice! <img src=x onerror=alert(1)>`. Trace it from `_flatten_replies`'
   `record.get("text", "")` to the rendered page. Where is it neutralized?
   *Answer:* nowhere in Python — `{{ reply.text }}` in the template auto-escapes
   it to `&lt;img …&gt;`. Confirm with the `assertNotContains(... html=False)`
   assertion in `test_reactions_section_rendered_with_comments`. Now imagine
   someone adds `|safe`. What did they just open?

5. **Hands-on (forward compat).** A new app `com.example.applause` starts linking
   to your document. Without touching `reactions.py`, what does the counts row
   show? *Answer:* `N com.example.applause` — `KNOWN_SOURCES.get(collection,
   collection)` falls back to the raw NSID, so the count appears immediately,
   just unlabeled. Add one line to `KNOWN_SOURCES` to give it a pretty name.

6. **Read the contract.** Open the Constellation API notes at
   <https://github.com/at-microcosm/links> and the `getPostThread` schema at
   <https://docs.bsky.app/docs/api/app-bsky-feed-get-post-thread>. Which exact
   JSON paths does `_parse_constellation` / `fetch_thread` assume, and which are
   the ones the docstring warns you to verify live? (This is the
   "written-against-docs, not-yet-round-tripped" caveat in action.)

## Verify it yourself

```bash
git checkout learn/02-reactions
python -m pytest tests/test_atproto_reactions.py -q       # network fully mocked
git show 368ac32 -- src/django_mosaic/atproto/reactions.py   # the two-source client
git show 368ac32 -- src/django_mosaic/atproto/templates/atproto/reactions.html
```

The tests cover: thread parsing + flattening + depth, thread failure → `None`,
thread caching (`fetch.assert_called_once`), Constellation parse/label/dedupe,
the unwrapped+list shape, index failure → `[]`, the full page render (with the
`<script>` escaping assertion), the section's absence on an unsynced post, and
`200` when every source is down.

## Glossary

- **AppView** — a service that ingests one app's slice of the firehose and
  serves materialized views (threads, feeds, counts). `public.api.bsky.app` is
  Bluesky's public one.
- **`getPostThread`** — the AppView method returning a post with its aggregate
  counts and a nested tree of replies.
- **Companion post** — the `app.bsky.feed.post` mosaic created for your article
  in PR 1; its reply thread *is* your comment section.
- **Backlink index** — a service that answers the inverse of a normal query:
  "which records anywhere link to this target?" **Constellation** is the one used
  here.
- **Backlink target** — the thing others link to: an **AT-URI**
  (`at://did/collection/rkey`) or a **canonical URL**. This PR queries both.
- **Double counting** — the same reaction reported by two sources (Bluesky likes
  seen by both the AppView and Constellation); avoided by popping the bsky
  collections from the index result.
- **Tolerant parser** — a reader that type-checks before traversing and skips
  what it doesn't understand, so malformed input yields missing data, not an
  exception.
- **Graceful degradation** — the property that an upstream failure renders less,
  never errors. Here: an empty section, always a `200`.
- **TTL** — time-to-live; the per-entry cache expiry (`cache.set(k, v, seconds)`).
- **h-feed / h-entry** — Microformats2 classes that mark the comment section up
  as machine-readable for other consumers.
