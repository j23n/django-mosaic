# PR 5 — De-singletonize the engine + read-only preview mode

> **Stack:** 5/12 · **base:** `learn/04-hardening` · **Commits:**
> `cc70a33…f595136` (inclusive, M0–M1) · **What it adds:** an explicit per-identity
> object (with DID-scoped caches) replacing the settings singleton, a
> `/@<handle>` route that renders *anyone's* public ATmosphere content, and a
> standalone preview-service landing page with a waitlist, per-IP throttle,
> and `noindex`.

## The one-sentence version

Every read path stops reading the owner's handle out of global settings and
starts taking an explicit `Identity(handle, did, pds_url)` argument — so the
*same* code that renders the owner's site can render `/@alice.bsky.social`, a
read-only view of any account's public repo, and that one refactor is the thing
that makes multi-tenant hosting possible at all.

## Learning objectives

**ATProto**

- Read **any** actor's public repo, not just the owner's — the shift from a
  single-account *bridge* to a multi-account *AppView*. `describeRepo` +
  `listRecords` against an arbitrary DID's PDS is all it takes, because reads
  are unauthenticated.
- **DID-scoped caching**: cache on the immutable **DID**, never the reassignable
  **handle**. Understand what breaks if you key a cache on the handle.
- Why **PDS endpoints pulled from a DID document are untrusted input** the
  moment you resolve arbitrary handles, and must be SSRF-validated before you
  `GET` them.

**Python / Django**

- **Dependency injection vs. a settings singleton** — passing an explicit
  identity object down the call graph instead of reading globals, and why that
  is the *precondition* for multi-tenancy (this PR's Deep dive).
- A cache-based **per-IP fixed-window throttle** (`cache.add` + `cache.incr`).
- **Honeypot** form fields for cheap spam filtering.
- **`noindex` / `X-Robots-Tag`** for surfaces that render other people's
  content.

## Grounding: official docs

Read these first; the code is a thin client over them.

- DID — the **immutable** account id — <https://atproto.com/specs/did>
- Handle — the **reassignable** pointer — <https://atproto.com/specs/handle>
- Repository / reading records (`describeRepo`, `listRecords`) —
  <https://atproto.com/specs/repository>
- Overview & mental model — <https://atproto.com/guides/overview>
- OWASP: Server-Side Request Forgery —
  <https://owasp.org/www-community/attacks/Server_Side_Request_Forgery>
- Django caching (backends, `add`/`incr`, timeouts) —
  <https://docs.djangoproject.com/en/stable/topics/cache/>
- `X-Robots-Tag` / `noindex` —
  <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Robots-Tag>

## Motivation: three design-notes commits, then the pivot

Before any code, this PR lands three design documents (`cc70a33`, `cee083d`,
`c63762b`). Read the commit messages; you don't need every word of the docs, but
you need the arc, because it explains *why* the rest of the course exists.

- **`cc70a33` — reposition mosaic as a personal ATmosphere aggregator.** The
  identity shifts from "a blog engine with an ATProto bridge" to "**a personal
  AppView of your PDS, rendered as your own website**." All your ATmosphere
  content — posts, photos, repos, books, scrobbles, events — presented in one
  place instead of scattered across bsky.app, tangled.org, bookhive, and
  friends. *Many tiles, one picture; the name finally earns itself.* The
  template-per-NSID registry (PR 3) becomes the theming system; the Jetstream
  listener (PR 11) graduates from nice-to-have to core.
- **`cee083d` — competitive landscape + productization.** A survey of the
  personal-ATmosphere-site space (blento, at-home, dame.is, explorers) finds
  that *multi-lexicon aggregation as a product is unoccupied*. Productization is
  three shippable phases: (A) make the OSS single-user engine feel like a
  product, (B) "anyone can run it" via Docker + one-click hosts **plus an
  instant preview mode — type a handle, see their home** — which is both the
  demo and the growth loop, and (C) a hosted multi-tenant SaaS.
- **`c63762b` — the hosted (SaaS) plan.** Mosaic Hosted is "**a renderer with
  knobs, not a website editor**"; the user's site config lives as records in
  *their own* PDS (no lock-in, near-stateless SaaS); onboarding is "enter any
  handle → instant read-only preview → claim via OAuth." Crucially, it names the
  **prerequisite refactor (M0)**:

  > The engine currently reads a singleton identity from `MOSAIC_ATPROTO`
  > settings; every read path (`lexicons`, `reactions`, well-known views,
  > templates) must take identity from a request context instead. This refactor
  > also unlocks the OSS "preview any handle" mode — same code path.

That paragraph *is* this PR. Everything after it — OAuth (PR 6), the tenant
registry and host-header routing (PR 7), the in-PDS dashboard (PR 8) — assumes a
request can decide *whose* repo it is rendering. Today it can't: the identity is
a global. M0 fixes that; M1 ships the first thing the fix makes possible.

## Background: bridge → AppView, and why identity must become a value

In PR 1 mosaic was a **bridge**: one owner, one handle in settings, writing
*their* posts to *their* repo. Reads (PR 3's lexicon pages) also assumed the
owner — `identity()` resolved `conf.get_setting("HANDLE")` and cached the single
result under one fixed key. That is a **singleton**: one implicit identity,
reached by reaching into global state from anywhere in the stack.

An **AppView** reads *many* actors' repos. Bluesky's AppView indexes the whole
network; mosaic's is humbler — it renders one repo at a time — but the shape is
the same: *the repo being rendered is a parameter of the request, not a global.*
Reads make this cheap: **reading is open, writing is authenticated** (the
overview's core asymmetry). To render `alice.bsky.social` you need no
credentials — just resolve her handle to `(did, pds_url)` and hit two
unauthenticated XRPC endpoints:

```
describeRepo(repo=did)                 -> which collections exist
listRecords(repo=did, collection=…)    -> the records themselves
```

So the whole feature reduces to: *turn the implicit owner identity into an
explicit value, thread it through every read function, and let a view pass a
different one.* That value is `Identity`.

## Guided tour of the diff (read in this order)

### 1. `atproto/identity.py` — the new value object (start here)

The whole refactor pivots on 49 new lines:

```python
@dataclass(frozen=True)
class Identity:
    handle: str
    did: str
    pds_url: str


def resolve(handle):
    """Resolve any handle to an Identity, cached per handle."""
    cache_key = f"mosaic_atproto:identity:{handle}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    did, pds_url = resolve_identity(handle)
    identity = Identity(handle=handle, did=did, pds_url=pds_url)
    cache.set(cache_key, identity, IDENTITY_CACHE_SECONDS)
    return identity


def owner():
    """The site owner's Identity, or None when no handle is configured."""
    handle = conf.get_setting("HANDLE")
    if not handle:
        return None
    return resolve(handle)
```

Two functions, one type. `resolve(handle)` works for *any* handle;
`owner()` is just `resolve(HANDLE)` — the owner is no longer special-cased in
the plumbing, it's one call site that happens to read a setting. `frozen=True`
makes `Identity` hashable and immutable: once resolved it's a value you pass
around, not state you mutate. This is the seam the rest of the PR threads
through.

### 2. `atproto/client.py` — override scoping + the SSRF guard

`resolve_identity` gains two changes, both forced by "we now resolve strangers'
handles."

**Override scoping.** In PR 1 the `DID`/`PDS_URL` settings overrides applied to
*any* resolution:

```python
did = conf.get_setting("DID")          # old: unconditional
pds_url = conf.get_setting("PDS_URL")
```

That was harmless when the only handle ever resolved was the owner's. The moment
preview mode resolves `alice.bsky.social`, an owner override would return the
**owner's** DID/PDS for *Alice's* handle — a wrong-repo read. The fix gates the
overrides on identity:

```python
is_owner = handle == conf.get_setting("HANDLE")
did = conf.get_setting("DID") if is_owner else ""
pds_url = conf.get_setting("PDS_URL") if is_owner else ""
```

Overrides are a trusted convenience for the *owner's* self-hosted/air-gapped
setup; they must never leak into anyone else's resolution. (Test:
`test_owner_overrides_do_not_leak_to_other_handles`.)

**The SSRF guard.** A DID document's PDS `serviceEndpoint` is data the *account
owner* controls, not you. Once you resolve arbitrary handles, that string is
**untrusted input** — a malicious DID doc could point "the PDS" at
`http://169.254.169.254/…` (cloud metadata) or `http://localhost:5432` and your
server would dutifully `GET` it. That's textbook SSRF (see the OWASP link). The
new `_validate_pds_url` rejects the dangerous shapes:

```python
def _validate_pds_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise AtprotoError(f"Refusing non-https PDS endpoint: {url}")
    host = parts.hostname or ""
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        raise AtprotoError(f"Refusing PDS endpoint host: {url}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass          # a hostname, not an IP literal — fine
    else:
        raise AtprotoError(f"Refusing IP-literal PDS endpoint: {url}")
    return url
```

Require `https`, reject `localhost`/`.local`/`.internal`, reject bare IP
literals (which is how you'd reach `127.0.0.1` or link-local metadata). Note
*where* it's called: only on the PDS discovered from a resolved DID document —
**not** on the owner's configured `PDS_URL` override, which stays trusted so a
self-hoster's `http://localhost:3000` PDS keeps working.

> **This is a partial guard, on purpose.** It blocks the obvious cases but not a
> hostname that *resolves* to a private IP (DNS rebinding), and it doesn't
> re-validate after redirects. PR 1's review question already flagged that
> `_raise_for_error` ignores `3xx`; PR 12's adversarial pass tightens both. For
> now: name the untrusted boundary, block the easy attacks, and move on — but
> know it's a floor, not a ceiling.

### 3. `atproto/lexicons.py` — threading `identity` through every read

This is the mechanical heart of the refactor. The old singleton `identity()` (a
cached `(did, pds_url)` tuple under one fixed key) is **deleted**. In its place,
a private helper and a rule:

```python
def _target(identity):
    """The Identity to read from (explicit one, else the site owner's)."""
    if identity is not None:
        return identity
    try:
        return identity_mod.owner()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"owner identity resolution failed: {e}")
        return None
```

Every read function now takes `identity=None` and resolves its target through
`_target`: pass one explicitly (preview), or fall back to the owner (the site).
Then the pivotal detail — **caches are keyed on `target.did`, not the handle**:

```python
def describe_repo(identity=None):
    target = _target(identity)
    if target is None:
        return []
    cache_key = f"mosaic_atproto:collections:{target.did}"   # DID, not handle
    ...

def list_records(collection, identity=None, limit=MAX_RECORDS):
    target = _target(identity)
    if target is None:
        return []
    cache_key = f"mosaic_atproto:records:{target.did}:{collection}:{limit}"
    ...

def blob_url(blob, identity=None):
    ...
    target = _target(identity)
    if target is None:
        return ""
    return f"{target.pds_url}/xrpc/com.atproto.sync.getBlob?did={target.did}&cid={cid}"
```

Read `describe_repo` closely — it's new here. `com.atproto.repo.describeRepo`
returns the list of collection NSIDs *actually present* in a repo, which is how
preview mode knows which sections to build without guessing. Everything degrades
to `[]`/`""` on failure (a PDS outage must never 500 the page), exactly the
"fail closed to a no-op" discipline from PR 1.

> **Why DID and not handle?** A DID is permanent; a handle is a lease. If you
> cached Alice's records under `…:records:alice.bsky.social` and Alice later
> renamed to `alice.com` — or worse, *gave up* `alice.bsky.social` and Bob
> claimed it — the handle-keyed entry would now serve Bob's viewer **Alice's**
> content, or serve Alice stale data under a name that's no longer hers. Keying
> on the immutable DID makes the cache correct across every handle change,
> forever. `Identity.resolve` still caches per *handle* (that's the
> handle→DID lookup, and it's fine for it to expire when a handle moves), but
> every cache that holds actual repo *content* keys on the DID. This is the
> single most important line-level habit in the PR. (Test:
> `test_records_cached_per_identity`.)

### 4. The template changes — where the singleton was hiding in plain sight

Two lexicon partials had baked-in owner assumptions. `sh.tangled.repo.html` is
the tell:

```diff
-{% load mosaic_atproto %}
-  <a href="https://tangled.org/@{% atproto_handle %}/{{ record.value.name }}">
+  <a href="https://tangled.org/@{{ identity.handle }}/{{ record.value.name }}">
```

The `{% atproto_handle %}` tag reads the owner's handle from settings. In
preview mode that would render **the owner's** tangled.org link on **Alice's**
repo — a subtle, plausible-looking bug that ships wrong URLs, not an error.
Threading `identity` into the template context and using `identity.handle` fixes
it. Same story for `buzz.bookhive.book.html`, where blob (cover image) URLs must
point at the *previewed* repo's PDS:

```diff
-  src="{{ record.value.cover|blob_url }}"
+  src="{{ record.value.cover|blob_url:identity }}"
```

The `blob_url` filter gains an optional identity argument
(`{{ blob|blob_url:identity }}`), falling back to the owner when absent. The
test `test_preview_links_and_blobs_use_previewed_identity` exists precisely to
lock this down — it's the class of bug de-singletonizing is meant to kill.

### 5. `atproto/preview.py` — assembling the preview

`build_sections(identity)` is the AppView in miniature:

```python
def build_sections(identity):
    present = lexicons.describe_repo(identity)          # what's in the repo
    sections = []
    seen_titles = set()
    for collection, title in lexicons.PREVIEW_COLLECTIONS.items():
        if collection not in present:
            continue
        records = lexicons.list_records(collection, identity=identity,
                                        limit=RECORDS_PER_SECTION)
        if not records:
            continue
        if title in seen_titles:   # two scrobblers share "Listening" — keep first
            continue
        seen_titles.add(title)
        sections.append({... "record_template": [
            f"lexicons/{collection}.html", "lexicons/generic.html"]})
    other = sorted(c for c in present
                   if c not in known and not c.startswith(("app.bsky.", "chat.bsky.")))
    return sections, other
```

`PREVIEW_COLLECTIONS` maps a curated set of NSIDs to display titles (Writing,
Projects, Books, Photos, Listening, Events, Reviews, Links). The logic: *for
each known collection actually present, list its latest records and render them
through the existing per-NSID partial*; everything else in the repo (minus the
Bluesky/chat firehose noise) is listed by name so the preview shows the repo's
full breadth. Notice it reuses PR 3's template registry unchanged — the payoff
of having built rendering as "template-per-NSID" is that preview mode is mostly
glue. `fetch_profile` pulls the Bluesky profile header (`getProfile` on the
public AppView) for decoration and degrades to `None` on failure.

### 6. `views.py` + `urls.py` — the `/@handle` route (M0)

```python
def preview(request, handle):
    if not preview_mod.enabled():
        raise Http404("Preview mode is disabled.")
    handle = handle.strip().lstrip("@").lower()
    try:
        identity = identity_mod.resolve(handle)
    except AtprotoError as e:
        raise Http404(f"Could not resolve handle: {handle}") from e
    profile = preview_mod.fetch_profile(identity)
    sections, other_collections = preview_mod.build_sections(identity)
    return render(request, "atproto/preview.html", {...})
```

`path("@<str:handle>", preview, name="atproto-preview")`. The view is the
*only* place that decides whose repo to render — it resolves an explicit
`Identity` and hands it down. Opt-in via `MOSAIC_ATPROTO["PREVIEW"]` (default
`False`), so a normal blog install never grows a `/@…` route. Unresolvable
handles and disabled mode both 404.

### 7. M1 — the preview *service*: landing, waitlist, throttle, noindex

M0 shipped the capability; `f595136` turns an instance into a product surface —
the "type a handle, see your home" funnel from the design notes.

**Startup-built routes.** When `PREVIEW_LANDING` is on, the landing page takes
over the site root, wired at import time like PR 3's lexicon pages:

```python
if preview_mod.landing_enabled():   # enabled() AND PREVIEW_LANDING
    urlpatterns += [
        path("", preview_landing, name="atproto-preview-landing"),
        path("preview/waitlist", waitlist_signup, name="atproto-waitlist"),
    ]
```

Building routes conditionally at startup (not per-request) means a normal blog
install's URLconf is byte-for-byte unchanged — the feature is invisible unless
you asked for it.

**Per-IP throttle.** An internet-facing preview endpoint fans out XRPC calls to
strangers' PDSes; you don't want it to be a free amplifier. `allow_request` is a
cache-based **fixed-window** counter:

```python
def allow_request(ip):
    limit = conf.get_setting("PREVIEW_RATE_LIMIT")   # default 30/min, 0 disables
    if not limit:
        return True
    cache_key = f"mosaic_atproto:preview_rate:{ip}"
    if cache.add(cache_key, 1, timeout=60):   # first hit this window: create + count 1
        return True
    try:
        count = cache.incr(cache_key)         # atomic increment
    except ValueError:                        # key expired between add and incr
        return True
    return count <= limit
```

The pattern is worth internalizing: `cache.add` returns `True` only if the key
was absent, atomically starting the window; `cache.incr` atomically bumps it;
the 60s timeout *is* the sliding-off of the window. Over the limit, the view
returns **429**. It's coarse (a fixed window lets a burst straddle the boundary)
and keys on `REMOTE_ADDR` (so behind a proxy you must set the real client IP,
e.g. nginx `real_ip`) — but it's a genuine floor that ships before any
proxy-level limiting exists. (See the Django caching docs for `add`/`incr`
atomicity guarantees per backend.)

**`noindex`.** You're rendering *other people's* content on *your* domain; you
must not let Google index it as yours. Both belt and suspenders:

```python
response["X-Robots-Tag"] = "noindex"                       # in the view
```
```html
{% block head_extra %}<meta name="robots" content="noindex">{% endblock %}
```

The header covers non-HTML responses and is authoritative; the meta tag covers
crawlers that only parse HTML. (MDN's `X-Robots-Tag` page explains why the
header is the more robust of the two.)

**Waitlist + honeypot.** A `WaitlistSignup` model (unique `contact`,
admin-registered) collects interest. The signup endpoint is a spam magnet, so it
carries a **honeypot**:

```html
<input type="text" name="website" autocomplete="off" tabindex="-1"
       aria-hidden="true" style="position: absolute; left: -9999px;">
```
```python
@require_POST
def waitlist_signup(request):
    if not preview_mod.landing_enabled():
        raise Http404(...)
    if request.POST.get("website"):
        # Honeypot filled in by a bot: silently pretend success.
        return redirect(f"{reverse('atproto-preview-landing')}?joined=1")
    contact = (request.POST.get("contact") or "").strip()[:320]
    if contact:
        WaitlistSignup.objects.get_or_create(contact=contact)
    return redirect(...)
```

A field named `website`, hidden from humans (off-screen, `aria-hidden`,
`tabindex=-1`), invisible to real users but happily filled by dumb bots. If it's
non-empty, *silently pretend success* — don't tell the bot it failed. Note
`get_or_create` on the unique `contact` makes double-submits idempotent, and the
`require_POST` + `landing_enabled()` gates keep the endpoint inert unless the
service mode is on. The landing template is standalone (doesn't extend
`base.html`) so it reads as a service, not a blog.

## Deep dive: the singleton → injected-identity refactor (the pivot of the whole course)

This is the architectural move everything after it depends on, so it's worth
being precise about *what* changed and *why* it unlocks the rest of the stack.

**The before.** PR 1–4's read code reached a single implicit identity by
reading global settings:

```python
def identity():                       # returns ONE (did, pds_url)
    cached = cache.get("mosaic_atproto:identity")   # ONE fixed key
    if cached: return cached
    resolved = resolve_identity(conf.get_setting("HANDLE"))   # THE handle
    cache.set("mosaic_atproto:identity", resolved, ...)
    return resolved
```

Every reader — `list_records`, `blob_url`, the templates — called this
zero-argument function. The identity was *ambient*: not passed in, but pulled
out of global state at the point of use. That is a **singleton** (one instance,
globally reachable). It's fine right up until you need *two*.

**The after.** Identity becomes a **value** created at the top of the request
and **injected** downward:

```
view: identity = identity_mod.resolve(handle)   # or lexicons.owner_identity()
  └─ lexicons.list_records(collection, identity=identity)
  └─ lexicons.blob_url(blob, identity=identity)
  └─ template context: {"identity": identity}
       └─ {{ blob|blob_url:identity }}, {{ identity.handle }}
```

Nothing below the view reads `MOSAIC_ATPROTO["HANDLE"]` anymore. The `owner()`
helper still *exists*, but it's now just one possible argument value, not a
hardwired assumption baked into the plumbing. This is textbook **dependency
injection**: instead of a function reaching out to fetch its dependency from a
global, the dependency is handed in by whoever calls it.

**Why this is the precondition for everything after.** A singleton identity can
serve exactly one tenant, because "who am I rendering?" is answered by a global
that has one value. Ask the question the hosted plan needs — *this request is for
`alice.com`, that one is for `bob.dev`* — and a global cannot answer it: two
concurrent requests would fight over one variable. Once identity is a per-request
value, the answer becomes *local to the request*, and every downstream layer
already accepts it as a parameter. Concretely, this single refactor is what lets:

- **PR 5 M1** render `/@anyone` — pass a different `Identity`. (Shipped here.)
- **PR 7** route by Host header — middleware resolves `alice.com → Identity`
  and stashes it on the request; the *same* read functions serve it. No further
  changes to `lexicons.py` needed — that's the whole point.
- **PR 8** load per-tenant settings, **PR 6** bind OAuth sessions per identity,
  **PR 11** invalidate caches per DID on the firehose.

You could not build any of those on top of a global. The reason this
unglamorous, mostly-mechanical PR sits at the exact middle of the twelve is that
it's the hinge: PRs 1–4 are the single-owner engine; PRs 6–12 are the
multi-tenant product; **this** is the refactor that converts one into a
substrate for the other. The DID-scoped caching is the same idea applied to
state: a cache keyed on a global (`…:identity`, `…:records:{collection}`) is a
singleton cache and would cross-contaminate tenants; keying on `target.did`
makes each tenant's cache naturally disjoint. *Inject the identity, key state on
the DID* — internalize those two habits and the rest of the course is
elaboration.

> **The general lesson.** A settings singleton is the right call for a
> single-owner app (PR 1 was correct to use one — YAGNI). It becomes technical
> debt the instant you need a second tenant, and the cost of paying it down is
> proportional to how many call sites reached into the global. mosaic kept that
> cost low by having *one* accessor (`conf.get_setting`) and *one* resolver
> (`identity()`); the refactor is "add a parameter, thread it, delete the
> global accessor." When you build the single-tenant version of anything,
> funnel ambient state through one seam so that de-singletonizing later is a
> parameter, not a rewrite.

## Design decisions & "why not X"

- **Why is `PREVIEW` opt-in and default-off?** An installed-but-unconfigured
  sub-app must be inert (PR 1's rule). A blog owner didn't ask to run an
  arbitrary-handle AppView with an outbound-fetch surface; they opt in when they
  want the demo/service.
- **Why validate the discovered PDS but trust the configured override?** Trust
  boundary. The override is *your* setting; the DID-document endpoint is a
  stranger's data. Trusting the override keeps `http://localhost` self-hosting
  working; validating the discovered one closes the SSRF hole preview opens.
- **Why an in-app throttle when "you should rate-limit at the proxy"?** Defense
  in depth and safe defaults. A one-command deploy shouldn't be an open
  amplifier before the operator gets around to nginx. `PREVIEW_RATE_LIMIT=0`
  turns it off when the proxy owns the job.
- **Why store the waitlist in Django when the whole thesis is "no non-ATProto
  state"?** A pre-launch email list is operational data about *prospects*, not
  user content — it has no repo to live in. The hosted plan keeps *user* content
  and config in the PDS; this is the allowed exception (tenant registry, billing,
  and now signups live in Postgres).

## Exercises

1. **Break the cache on purpose.** Revert `describe_repo`'s cache key from
   `…:collections:{target.did}` to `…:collections:{target.handle}`. Now write
   the failing test: preview Alice, let it cache, then simulate Alice releasing
   her handle and Bob claiming it (same handle string, different DID). What does
   the second viewer see? Why does DID-keying prevent it?
2. **Find the leaked owner.** Before the template fix, `sh.tangled.repo.html`
   used `{% atproto_handle %}`. Trace what URL `/@alice.bsky.social` would have
   rendered for a Tangled repo, and explain why it's a *silent* bug (wrong
   output, no error). Which test would have caught it?
3. **Defeat the SSRF guard on paper.** `_validate_pds_url` accepts any
   non-literal hostname over https. Name two ways a hostname could still reach an
   internal address (hint: what does the name *resolve* to, and what happens on a
   redirect?). Which later PR closes these, and what would the fix look like?
4. **Stress the throttle.** With `PREVIEW_RATE_LIMIT=30`, describe a request
   pattern that gets **59** preview loads through in ~1 second despite the
   "30/min" label. Why does a fixed window allow this, and what does a sliding
   window or token bucket change?
5. **Hands-on.** Set `MOSAIC_ATPROTO = {"HANDLE": "...", "PREVIEW": True}` in a
   scratch project, run the server, and hit `/@<some-real-handle>` (try a
   handle with Tangled repos or BookHive books). Confirm the section links and
   blob image URLs point at *that* account's PDS, not yours.

## Verify it yourself

```bash
git checkout learn/05-preview
python -m pytest tests/test_atproto_preview.py tests/test_atproto_landing.py -q
git show 98f6b3d -- src/django_mosaic/atproto/identity.py   # the value object
git show 98f6b3d -- src/django_mosaic/atproto/lexicons.py   # identity threaded through
git show f595136 -- src/django_mosaic/atproto/views.py      # throttle + noindex + waitlist
```

Read `test_owner_overrides_do_not_leak_to_other_handles`,
`test_records_cached_per_identity`, and the four SSRF cases
(`test_http_rejected`, `test_ip_literal_rejected`,
`test_localhost_and_internal_rejected`,
`test_https_public_hostname_accepted`) — each pins one claim from this lesson.

## Glossary

- **AppView** — a service that reads and renders repo data across accounts.
  mosaic's is a per-request, one-repo-at-a-time AppView.
- **`Identity`** — the injected value: `(handle, did, pds_url)`. Replaces the
  settings singleton on every read path.
- **DID-scoped cache** — a cache keyed on the immutable DID, so it stays correct
  across handle changes and never crosses tenants.
- **SSRF** — Server-Side Request Forgery: coercing your server into fetching an
  internal/attacker-chosen URL. Here, via a hostile DID document's PDS endpoint.
- **Honeypot field** — a hidden form input real users leave blank; a filled one
  marks a bot.
- **Fixed-window throttle** — count requests per key per time bucket; simple,
  atomic over a cache backend, coarse at boundaries.
- **`noindex` / `X-Robots-Tag`** — tell crawlers not to index a page; used
  because preview renders other people's content.
