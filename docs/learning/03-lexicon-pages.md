# PR 3 — Lexicon collection pages (`/projects`, `/books`, …)

> **Stack:** 3/12 · **base:** `learn/02-reactions` · **Commit:** `1f25e7a`
> · **What it adds:** the "personal AppView" half of the bridge — configured
> root-level pages that render *any* collection from your ATProto repo at
> request time (Tangled repos, BookHive books, …) with **no local models**.

## The one-sentence version

You point a URL slug at a collection NSID (`/projects` →
`sh.tangled.repo`), and on each request mosaic does two public XRPC reads —
resolve your identity, `listRecords` your repo — then renders each record's raw
JSON through a **template chosen by NSID**, falling back to a generic
`<details>` dump for collections it has never seen.

## Learning objectives

**ATProto**

- Know what a **Lexicon** schema and an **NSID** are, and that the NSID string
  *is* the contract that lets two apps share a record type.
- Read a whole collection with **`com.atproto.repo.listRecords`** — the
  unauthenticated repo-read that needs only a DID, no session.
- Understand why **TID rkeys are timestamp-ordered**, so `reverse=true`
  (descending rkey) means "newest first" with **no server-side sort**.
- See the **personal AppView** pattern: you render *your own repo's* records on
  *your own* site, indexing nothing globally.
- Read **other apps' lexicons** you never designed — Tangled
  (`sh.tangled.repo`) and BookHive (`buzz.bookhive.book`) — the payoff of
  **app-neutral data**: the record is portable, the renderer is yours.
- Turn a **blob ref** into an image URL with `com.atproto.sync.getBlob`.

**Python / Django**

- **Request-time rendering with zero persistence** — a view that fetches +
  renders, backed only by the cache, never a model or migration.
- A **template registry keyed by NSID** implemented as *nothing but* a Django
  template-loader search list — a lookup table with a guaranteed fallback.
- Writing **custom template filters** (`blob_url`, `tabbed`, `half_stars`,
  `token_name`) to keep view logic out of templates.
- Making templates **overridable by consumers** at three granularities
  (whole page / one record / the frame) via the loader's app-vs-project order.

## Grounding: official docs

Read the shape of the data before the code that renders it.

- Lexicon (the schema language) — <https://atproto.com/specs/lexicon>
- NSID (the reverse-DNS type name) — <https://atproto.com/specs/nsid>
- Record key / **TID** ordering — <https://atproto.com/specs/record-key>
- Repository (collections of records) — <https://atproto.com/specs/repository>
- `com.atproto.repo.listRecords` + the rest — the HTTP reference at
  <https://docs.bsky.app/docs/category/http-reference>
- Blobs + `getBlob` — <https://atproto.com/specs/blob>
- AT-URI (where the rkey comes from) — <https://atproto.com/specs/at-uri-scheme>
- The mental model, if you skipped it — <https://atproto.com/guides/overview>
- Django custom template tags/filters —
  <https://docs.djangoproject.com/en/stable/howto/custom-template-tags/>
- Django template overriding —
  <https://docs.djangoproject.com/en/stable/howto/overriding-templates/>

## Background: the model this PR implements

PR 1 was the **write** side: mosaic pushed your posts *into* your repo. This PR
is the **read** side, and it reads collections mosaic never wrote.

An ATProto **repository** is a signed store of **records** grouped into
**collections**. A collection is named by an **NSID** — a reverse-DNS string
like `sh.tangled.repo` or `buzz.bookhive.book`. That name is declared by a
**Lexicon** schema owned by whoever controls the domain (`tangled.sh`,
`bookhive.buzz`). The crucial property: **the record is app-neutral**. When you
`git init` a repo on [Tangled](https://tangled.sh) (the domain the
`sh.tangled.repo` NSID reverses to) it writes a
`sh.tangled.repo` record into *your* PDS; when you shelve a book on
[BookHive](https://bookhive.buzz) it writes a `buzz.bookhive.book` record into
*your* PDS. Those records are yours, sitting in your repo, addressable by
AT-URI — and nothing stops *your* website from listing them.

That is the **personal AppView** pattern. A full AppView (like Bluesky's)
crawls the whole network's firehose and indexes millions of repos. A *personal*
AppView indexes exactly one repo — yours — at request time, with a plain HTTP
read. No firehose, no index, no database. The cost is one `listRecords` call
per page load (cached); the payoff is that `/projects` and `/books` on your site
are live views of data other apps maintain for you.

Reading is open (PR 1's asymmetry): `listRecords` needs no app password, no
session — just your DID to say *whose* repo, and the PDS URL to say *where*.
Both come from the same `resolve_identity(handle)` chain PR 1 built.

```
handle  --resolve_identity-->  (did, pds_url)          [cached 1h]
did     --listRecords(collection, reverse)-->  records  [cached 5m]
record.value  --template-by-NSID-->  HTML
```

## Guided tour of the diff (read in this order)

### 1. `atproto/conf.py` — one new key

The entire public surface stays a single `MOSAIC_ATPROTO` dict. This PR adds
`LEXICON_PAGES`, a map of **root slug → {collection, title}**:

```python
"LEXICON_PAGES": {
    "projects": {"collection": "sh.tangled.repo", "title": "Projects"},
    "books":    {"collection": "buzz.bookhive.book", "title": "Books"},
},
```

That is the whole configuration story: a slug becomes a URL, a collection NSID
becomes what it lists. The defaults ship these two; set the key to your own map
to change the pages, or to `{}` to have none.

### 2. `atproto/lexicons.py` — the core (new file)

This is the ATProto meat. Read it top to bottom; it is only ~120 lines.

- **`pages()`** returns `conf.as_dict().get("LEXICON_PAGES")`, falling back to a
  module-level `DEFAULT_PAGES` when the key is *absent* (note: an explicit `{}`
  is honored as "no pages" — `is not None`, not truthiness).
- **`read_enabled()`** is deliberately *weaker* than PR 1's `enabled()`:

  ```python
  return bool(HANDLE or (DID and PDS_URL))
  ```

  No app password. Reading a public repo needs only an identity. This is the
  read/write asymmetry made concrete — the read pages light up with just a
  handle, long before you configure credentials.
- **`identity()`** wraps `resolve_identity(HANDLE)` in a **1-hour cache**
  (`mosaic_atproto:identity`). Identity resolution (handle → DID → PDS) is the
  slow, stable part; caching it means the hot path is one `listRecords`.
- **`list_records(collection, limit=500)`** is the payload. Trace the loop:

  ```python
  params = {
      "repo": did,
      "collection": collection,
      "limit": min(100, limit - len(records)),
      "reverse": "true",   # rkeys are TIDs → reverse = newest first
  }
  ...
  data = xrpc_get(pds_url, "com.atproto.repo.listRecords", params)
  ```

  It pages 100 at a time (the PDS per-call max) following `cursor` until it hits
  `MAX_RECORDS = 500` or runs out. Each item is flattened to
  `{"uri", "cid", "rkey", "value"}`, where `rkey` is pulled off the AT-URI's
  last segment (`uri.rsplit("/", 1)[-1]`) and `value` is the raw record JSON the
  templates render. The result is cached **5 minutes**
  (`mosaic_atproto:records:{collection}`). The `try/except Exception → return []`
  is the same discipline as PR 1's signal: **a PDS outage degrades to an empty
  list, it never 500s the page.**
- **`blob_url(blob)`** builds a getBlob URL from a blob ref:

  ```python
  cid = ref.get("$link") if isinstance(ref, dict) else ref
  return f"{pds_url}/xrpc/com.atproto.sync.getBlob?did={did}&cid={cid}"
  ```

  A blob inside a record is *not* the bytes — it's a `{ref: {$link: <cid>},
  mimeType, size}` pointer (PR 1's upload side, in reverse). To show a BookHive
  cover you hand the browser a `com.atproto.sync.getBlob` URL against the
  owner's PDS + DID + CID, and the PDS streams the image. Tolerant by design:
  a non-dict blob or a missing ref returns `""`.

### 3. `atproto/urls.py` + `views.py` — routing and the view

The URLs are built **at import time** by iterating the configured pages:

```python
urlpatterns += [
    path(slug, lexicon_page, kwargs={"page": slug}, name=f"lexicon-{slug}")
    for slug in lexicons.pages()
]
```

So `/projects` and `/books` are real root-level routes, each carrying its slug
as a kwarg. The comment warns to include this urlconf **before** mosaic's
namespace catch-all so these win. (This import-time construction has a testing
consequence — see the tests below.)

`lexicon_page(request, page)` is the whole view, and it holds *no* logic beyond
wiring:

```python
config = lexicons.pages().get(page)
if config is None or not lexicons.read_enabled():
    raise Http404(...)
records = lexicons.list_records(config["collection"])
return render(request,
    [f"lexicons/pages/{page}.html", "lexicon-page.html"],   # page frame
    {
        "records": records,
        "record_template": [f"lexicons/{collection}.html",   # per-record
                            "lexicons/generic.html"],        # fallback
        ...
    })
```

Notice there are **two template search lists**, and neither names a single
file. Both rely on Django trying entries in order and using the first that
exists. That *is* the registry — see the deep dive.

### 4. The templates — `lexicon-page.html` + the `lexicons/` partials

- **`lexicon-page.html`** is the frame: extends `base.html`, prints the title,
  and loops the records, `{% include record_template %}` per row (so each
  record renders through whatever the view's `record_template` list resolves
  to). Empty collections print "Nothing here yet."
- **`lexicons/sh.tangled.repo.html`** renders one Tangled repo: `name`,
  `description`, `knot` (the git server hosting it), linked to
  `https://tangled.org/@{% atproto_handle %}/{{ record.value.name }}`.
  > As in PR 1, that `tangled.org/@handle/repo` URL shape in the shipped
  > template is a **best-effort guess** at Tangled's routing (and note it uses
  > `tangled.org`, not the `tangled.sh` authority domain — likely wrong) —
  > verify it against the live app before you rely on it; the record itself
  > only guarantees `name`/`knot`, not a URL.
- **`lexicons/buzz.bookhive.book.html`** renders one book: cover via
  `{{ record.value.cover|blob_url }}`, `authors` (via `tabbed`), `status` (via
  `token_name`), `stars` (via `half_stars`), and the `review`.
- **`lexicons/generic.html`** is the fallback for any collection with no
  dedicated partial: a `<details>`/`<summary>` disclosure showing the `rkey`
  and a `<dl>` of every field except `$type`, each `truncatechars:300`. Ugly
  but universal — it renders a collection type nobody anticipated (the tests
  point it at `fm.teal.alpha.feed.play`, Teal.fm scrobbles, and it works).

### 5. `templatetags/mosaic_atproto.py` — four filters + a tag

The partials lean on small filters so the templates stay declarative:

- **`atproto_handle`** (`simple_tag`) — the owner's configured handle, for the
  Tangled link.
- **`blob_url`** — thin wrapper over `lexicons.blob_url`.
- **`tabbed`** — BookHive stores authors as a **tab-separated** string; join on
  `", "` (dropping empties).
- **`half_stars`** — BookHive stores rating as **half-stars 1–10**; render as
  `int(value)/2` with `:g` formatting, so `9 → 4.5`. Bad input → `""`.
- **`token_name`** — a lexicon **token** value is a full reference like
  `buzz.bookhive.defs#finished`; take the trailing `finished` via
  `rsplit("#", 1)[-1]`.

Each filter encodes a *fact about someone else's lexicon* — the tab separator,
the half-star scale, the token syntax. That is what "reading another app's
lexicon" concretely means: you learn its field conventions and translate them
for display.

### 6. `templates/base.html` — a one-line footer fix

The footer's RSS link was `{% url 'feed' namespace %}`, which assumed every
page had a `namespace` in context. Lexicon pages don't (they render a
collection, not a mosaic namespace), so the link now degrades to
`{% url 'feed' 'public' %}` when `namespace` is unset. Small, but it's the
tell that these pages live *outside* mosaic's normal request context.

### 7. `tests/test_atproto_lexicons.py` — how it's tested

Same discipline as PR 1: `xrpc_get` is mocked, so no network. Assertions check
the rendered HTML (`assertContains(resp, "4.5★")`, `cid=bafycover`) and the
shaping (`records[0]["rkey"] == "3aaa"`). Two details worth internalizing:

- `test_results_cached` mocks `xrpc_get` and asserts `fetch.assert_called_once()`
  across two `list_records` calls — proving the 5-minute cache.
- `GenericFallbackTest` can't just `self.client.get("/scrobbles")`, because
  **routes are frozen at import time** from the default settings. It calls
  `lexicon_page(request, page="scrobbles")` directly under `override_settings`.
  That awkwardness is a real property of building URLs from settings — worth
  seeing before it bites you.

## Deep dive: why `reverse=true` is "newest first" for free

Most list APIs make you pass a sort key. `listRecords` doesn't need one, and
understanding *why* is understanding **TIDs**.

A record's **rkey** (record key) is its identity within a collection — the last
segment of its AT-URI (`at://did/coll/`**`3aaa`**). The most common rkey kind is
a **TID** (Timestamp IDentifier): a 13-character, base32-sortable encoding of
the microsecond timestamp when the record was created (plus a clock-id to break
ties). See <https://atproto.com/specs/record-key>. Two consequences fall out:

1. **Lexicographic order of TIDs == chronological order.** TIDs are designed so
   that string-sorting them is the same as time-sorting them. A repo stores
   records ordered by rkey; for TID collections that order is *already*
   chronological, ascending (oldest → newest).
2. **`listRecords` returns records in rkey order**, and its `reverse` parameter
   flips that order. So `reverse=true` gives **descending rkey = newest
   first** — with the PDS doing *no* sorting work, because the ordering is
   intrinsic to the key, not computed. `list_records`'s comment says exactly
   this: `# rkeys are TIDs (ascending in time); reverse => newest first`.

This is a recurring ATProto elegance: **identity carries information**. The key
isn't an opaque autoincrement; it's a sortable timestamp, so "give me my repo
newest-first" is a free consequence of how keys are minted — no `ORDER BY`, no
secondary index. (The caveat: not every collection uses TID rkeys — some use
`literal:self` singletons or custom keys — but the app-generated collections
here do, which is why `reverse` is the right and only sort this code needs.)

## Deep dive: the template-registry-by-NSID pattern

You'd expect "pick a renderer based on a type string" to be a dict:

```python
RENDERERS = {"sh.tangled.repo": render_tangled, "buzz.bookhive.book": ...}
renderer = RENDERERS.get(nsid, render_generic)
```

This PR does exactly that — but the dict is Django's **template loader**, and
the keys are **filenames**. The view never branches on the NSID. It builds a
*search list* and hands it to the include machinery:

```python
record_template = [f"lexicons/{collection}.html", "lexicons/generic.html"]
```

`{% include record_template %}` walks the list and renders the **first template
that exists**. So:

- `sh.tangled.repo` → `lexicons/sh.tangled.repo.html` exists → used.
- `fm.teal.alpha.feed.play` → no such file → falls through to
  `lexicons/generic.html`. **Guaranteed fallback, no `KeyError`, no `if`.**

Why this is better than a Python dict here:

- **The fallback is structural.** The generic template is the last list entry;
  an unknown NSID can't crash, it can only render plainly. That's the same
  "fail to a safe default" instinct as PR 1's `enabled()` no-op.
- **Registration is by convention, not code.** Adding support for a new
  collection is *creating a file named after its NSID* — no registry edit, no
  import. The NSID string, the protocol contract, is literally the lookup key
  on disk.
- **It's overridable by consumers for free.** mosaic is a reusable app, so its
  templates ship inside the package; a project's own template dir is searched
  **first**. That gives three override granularities, all just by dropping a
  file at the right path (see the Django overriding-templates doc):

  | Override file (in the consumer's templates) | Replaces |
  |---|---|
  | `lexicons/pages/<slug>.html` | the **whole page** for one slug |
  | `lexicon-page.html` | the **frame** for all lexicon pages |
  | `lexicons/<collection NSID>.html` | the **per-record** partial |

  Note the view already searches `lexicons/pages/<slug>.html` *before*
  `lexicon-page.html`, and `lexicons/<NSID>.html` *before* `generic.html`. The
  override points are the registry's search order; the consumer just fills a
  slot. No mosaic code runs to make a project's `books` page look bespoke.

The lesson: when your "which renderer?" keys are strings and your outputs are
templates, the template loader *is* your registry — ordered lookup, default
entry, and third-party extensibility, with none of it written by you.

## Design decisions & "why not X"

- **Why no models / migrations?** The record already lives, canonically, in the
  PDS. Mirroring it into a local table would mean a sync problem (staleness,
  deletes, backfill) for data you don't own. Rendering at request time behind a
  5-minute cache trades a little latency for zero persistence and always-fresh
  reads. This is the personal-AppView bet.
- **Why cache identity (1h) and records (5m) separately?** They change on very
  different clocks. Your handle→DID→PDS mapping is near-static; your book shelf
  isn't. Two TTLs let the cheap-to-hold, expensive-to-fetch identity stay warm
  while collection data refreshes often.
- **Why `reverse=true` instead of sorting in Python?** Because the PDS returns
  records in rkey order and TID rkeys are already chronological — sorting again
  would be redundant work on data that's *born* sorted (see the deep dive).
- **Why filters instead of preprocessing in the view?** `half_stars`,
  `tabbed`, `token_name` are display concerns tied to specific lexicons. Keeping
  them as filters means an overriding template can reuse or drop them
  independently, and the view stays a generic "fetch + render" with no
  per-collection knowledge.
- **Why `read_enabled()` and not PR 1's `enabled()`?** Gating reads on an app
  password would be wrong — reading a public repo is unauthenticated. Requiring
  the credential would keep `/projects` dark for a handle-only install. The
  weaker gate is the protocol being honest about what reads actually need.

> **Review question.** The record cache key is `mosaic_atproto:records:{collection}`
> — no DID in it. Why is that fine *today*, and what breaks when PR 5
> de-singletonizes this to render **any** actor's repo? (Answer: today there's
> exactly one owner, so `collection` uniquely identifies the data. Once a view
> can read a *preview* of someone else's repo, two DIDs' `sh.tangled.repo`
> lists would collide on the same key — PR 5 has to make the cache **DID-scoped**.
> The `identity` key has the same latent bug.)

## Exercises

1. **Trace the ordering.** Take the two Tangled test records (rkeys `3aaa`,
   `3bbb`). With `reverse=true`, which renders first, and why does that match
   "newest"? Now suppose one rkey were `self` (a singleton, not a TID) — would
   `reverse` still mean "newest first"? (See the record-key spec.)
2. **Add a collection.** Configure a `/scrobbles` page for
   `fm.teal.alpha.feed.play` (as the tests do). It renders through
   `generic.html`. Now write `lexicons/fm.teal.alpha.feed.play.html` in a scratch
   project's template dir showing `trackName` and the artist — confirm your file
   wins over the generic fallback with **no code change**.
3. **Follow a blob.** Given the BookHive test cover
   `{"ref": {"$link": "bafycover"}, ...}`, hand-build the getBlob URL
   `blob_url` produces. Which three values must the PDS combine to stream the
   image, and which of them is the *content address*? (Blob spec.)
4. **Spot the fragility.** `list_records` pages until `MAX_RECORDS = 500`. What
   happens to a repo with 600 Tangled repos? Is the cap a correctness bug or a
   product decision — and where would you surface "there are more"?
5. **Read a real lexicon.** Open the `buzz.bookhive.book` schema (find it via
   the NSID's domain, `bookhive.buzz`). Check `half_stars`/`tabbed`/`token_name`
   against the actual field definitions. Did mosaic guess the `stars` scale and
   `authors` separator correctly?

## Verify it yourself

```bash
git checkout learn/03-lexicon-pages
python -m pytest tests/test_atproto_lexicons.py -q       # network fully mocked
git show 1f25e7a -- src/django_mosaic/atproto/lexicons.py   # the read core
# With a real handle configured, load /projects and /books and watch one
# listRecords fire per collection (then nothing for 5 min — the cache).
```

## Glossary

- **Lexicon** — the schema language declaring record/method shapes; owned by
  whoever controls the NSID's domain.
- **NSID** — reverse-DNS type name (`sh.tangled.repo`); the app-neutral contract
  and, here, the template lookup key.
- **`listRecords`** — `com.atproto.repo.listRecords`, the unauthenticated read
  of one collection from one repo; paginated via `cursor`, ordered by rkey.
- **TID** — Timestamp IDentifier rkey; base32-sortable, so lexical order ==
  chronological order (hence `reverse=true` = newest first).
- **Personal AppView** — rendering *your own* repo's records at request time
  with no index or database, as opposed to a network-wide indexing AppView.
- **Blob / getBlob** — a record's binary attachment, stored as a `ref` (CID)
  and streamed on demand via `com.atproto.sync.getBlob?did=…&cid=…`.
- **Token** — a lexicon enum value written as `<nsid>#<name>`
  (`buzz.bookhive.defs#finished`); `token_name` strips it to `finished`.
- **Template registry** — the loader search list `[lexicons/<NSID>.html,
  lexicons/generic.html]`: first-existing-wins dispatch with a guaranteed
  fallback, extensible and overridable by filename alone.
