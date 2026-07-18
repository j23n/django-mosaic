# PR 10 — Composer: the write path, document pages, and a custom-CSS tier

> **Stack:** 10/12 · **base:** `learn/09-domains`
> **Commit:** `160ac4d` · **What it adds:** `/dashboard/write` — a tenant
> writes a markdown post straight into **their own** PDS as a
> `site.standard.document`, gets a `/posts/<rkey>` page for it, and can bolt a
> free-form `/custom.css` stylesheet onto their site — all with mosaic still
> holding **zero** site content server-side.

## The one-sentence version

The dashboard grows a composer: you type markdown, mosaic mints a **TID rkey
locally** (so the permalink is known *before* the write), ensures your
`site.standard.publication` record exists, and `putRecord`s a
`site.standard.document` into your repo over your OAuth grant — then renders it
back at `/posts/<rkey>` through a bleach-sanitized markdown pass, with a
`textContent` fallback for documents that some *other* app wrote.

## Learning objectives

**ATProto**

- **Mint a TID rkey yourself.** Understand what a TID *is* — a 13-char,
  base32-sortable encoding of a 64-bit timestamp — and *why* a client can safely
  mint one without asking the PDS.
- Call the `com.atproto.repo.*` write methods **as the signed-in user** (the
  OAuth session from PR 6), and see why this PR uses `putRecord` at a known rkey
  rather than `createRecord`.
- Write a `site.standard.document` from an app that is **not** the origin blog —
  and see the two-way **interop** contract: mosaic stuffs its own markdown into
  the `content` union under a private `$type`, but also fills `textContent` so
  foreign apps can render the doc, and *reads* foreign docs back through that
  same `textContent` fallback.
- **rkey-addressed record pages:** the record carries its own `path`
  (`/posts/<rkey>`), and the tenant route resolves that rkey back to the record.

**Python / Django**

- Safely render **untrusted markdown** — untrusted because the record can come
  from another app or another user's repo — via a bleach-style **allowlist**
  (`markdownify` with `BLEACH: True`).
- Serve **user-controlled CSS** without opening an injection vector: a standalone
  `text/css` response, `X-Content-Type-Options: nosniff`, **never inlined** into
  HTML so it can't become a `</style><script>` break-out.
- The write-form flow: gate → session → tenant, validate, re-render on error with
  the **draft preserved**, redirect/confirm on success.

## Grounding: official docs

Read these first; the code is a thin client over them.

- Record keys / **TIDs** (the whole basis of `generate_tid`) —
  <https://atproto.com/specs/record-key>
- Repository + the repo write model (`putRecord`, collections, rkeys) —
  <https://atproto.com/specs/repository>
- HTTP reference for `createRecord`/`putRecord`/`getRecord` —
  <https://docs.bsky.app/docs/category/http-reference>
- The `site.standard.*` document/publication lexicons —
  <https://standard.site/>
- OAuth — the auth that makes these writes *the user's* —
  <https://atproto.com/specs/oauth>
- MIME sniffing & `X-Content-Type-Options: nosniff` (MDN) —
  <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Content-Type-Options>
- XSS prevention / allowlist sanitization (OWASP) —
  <https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html>

## Background: the model this PR implements

Everything before PR 10 was a **read** side: mosaic resolved a tenant's identity
and *displayed* records that already lived in their repo (blog exports, other
apps' writes). This PR flips on the **write** side for hosted tenants — but keeps
the cardinal rule of the hosted design intact: **the content never touches
mosaic's database.** The composer opens the tenant's OAuth session and writes
into *their* PDS. mosaic is the pen, not the paper.

Two collections are involved, exactly as in PR 1's bridge but now written from
the hosted service on the user's behalf:

- `site.standard.publication` — a **singleton** at rkey `self`, describing the
  site (its `url` and `name`). Ensured on first publish so any standard.site
  reader can attribute the document to a site.
- `site.standard.document` — **one record per post**, keyed by a TID rkey.

The single non-obvious idea to hold onto: because the rkey is a client-mintable
TID, mosaic can decide the record's permalink (`/posts/<rkey>`) *before* it
writes, and bake that path *into* the record. The write and the URL are known in
the same breath.

## Guided tour of the diff (read in this order)

### 1. `hosted/composer.py` — the write path (read this whole file)

197 new lines and the heart of the PR. Read top to bottom.

**`generate_tid()`** — the local rkey mint. Deep-dived below; for now just note
that it needs no network and returns a 13-char sortable string.

**`site_url(tenant)`** — the canonical base URL, preferring a *verified* custom
domain (`custom_domain and domain_verified_at`) over the
`<subdomain>.<base_domain>` fallback. This only forms the absolute `url` handed
back to the composer UI; the record itself stores a **host-relative** `path`
(`/posts/<rkey>`), which is what keeps the document portable — it doesn't hardcode
where the site currently lives.

**`ensure_publication(session, tenant)`** — create-or-return the `self`
publication record. It `getRecord`s first (a cheap read); on `AtprotoError` (not
found) it `putRecord`s a fresh one. Returns the publication **AT-URI**, which the
document points at via its `site` field. Idempotent by construction: rkey `self`
means "the one publication for this repo."

**`publish(session, tenant, title, body_markdown, description="")`** — the flow:

1. **Validate.** Trim + cap `title` (300) and `description` (600); require both
   title and body; reject a body over `BODY_MAX_BYTES` (30 kB, measured on the
   UTF-8 encoding, not the character count). Failures raise `ComposerError`,
   which the view turns into a 400 with the draft preserved.
2. `ensure_publication(...)` → `publication_uri`.
3. `rkey = generate_tid()`, `path = f"/posts/{rkey}"`.
4. Build the `site.standard.document` record (below), then `putRecord` it under
   the minted rkey. An OAuth failure becomes a `ComposerError`.
5. `_invalidate_read_caches(session.did)` so the new post shows up on the home
   page immediately, then return `{rkey, path, url}`.

The record shape is the interop lesson in miniature:

```python
record = {
    "$type": at_conf.DOCUMENT_NSID,          # site.standard.document
    "site": publication_uri,                 # at://…/site.standard.publication/self
    "path": path,                            # /posts/<rkey>  ← its own permalink
    "title": title,
    "textContent": html.unescape(strip_tags(md.markdown(body_markdown))).strip(),
    "content": [                             # the open union
        {"$type": at_conf.get_setting("CONTENT_NSID"),   # blog.mosaic.content.markdown
         "markdown": body_markdown},
    ],
    "publishedAt": _iso_now(),
}
```

Two fields carry the same post for two audiences:

- **`content`** is standard.site's open list of typed content blocks. mosaic
  writes *one* block with its own private `$type`
  (`blog.mosaic.content.markdown`) holding the raw markdown source. An app that
  knows that `$type` gets the rich original.
- **`textContent`** is the app-neutral plain-text rendering: render the markdown
  to HTML, `strip_tags` it, `html.unescape` the entities, strip whitespace. This
  is what a foreign reader that has never heard of `blog.mosaic.content.markdown`
  falls back to. **You always fill it**, precisely so your document isn't opaque
  to the rest of the network.

> **This is the "write into a lexicon someone else defined" discipline.** Extend
> a shared type through its open union with your own namespaced block, but never
> leave the standard fields empty — they are the interop contract. mosaic is not
> the origin blog for these documents, yet it writes them so *any* standard.site
> app can read them.

**`get_document(identity, rkey)`** — the read path for the page. `getRecord`,
cache the `value` for `DOCUMENT_CACHE_SECONDS` (60s), and — critically — cache
the **miss** too (as `""`) so a 404-farming loop can't hammer the PDS. Note
`except Exception` here: the render path must degrade to "no such post," never
500.

**`document_markdown(value)`** — scan the `content` union for the first block
that is a dict with a `str` `markdown` field; return it or `None`. This is what
decides, at render time, whether we have a mosaic-authored source to re-render or
must fall back to `textContent`.

### 2. `hosted/views.py` — three new views

- **`tenant_document(request, rkey)`** — resolve the tenant's identity,
  `get_document`, 404 if absent, else render `hosted/document.html` with the
  record value, the extracted `markdown_source`, and the theme's
  `css_variables` / `has_custom_css`. On an identity-resolution failure it serves
  a 503 page rather than an error.
- **`tenant_custom_css(request)`** — the stylesheet endpoint (deep-dived below).
- **`dashboard_write(request)`** — the composer view. The gate ladder is worth
  memorizing: `conf.enabled()` → 404; `flow.current_session` is `None` →
  redirect to OAuth login carrying `?next=/dashboard/write`; no `Tenant` for this
  DID → redirect to claim. On `POST`, call `composer.publish`; a `ComposerError`
  re-renders `write.html` at **status 400 with `title`/`description`/`body`
  echoed back** (the writer doesn't lose their draft); success re-renders with a
  `published` dict linking the new permalink.

`dashboard(request)` also grows a `custom_css` field, passed through to
`site_settings.save(..., custom_css=...)`.

### 3. `hosted/site_settings.py` — the CSS lands in the settings record

`save()` gains a `custom_css=""` param; it's capped at `CUSTOM_CSS_MAX` (20k) and
only written as `record["customCss"]` when non-blank. The new **`custom_css()`**
accessor is defense-in-depth on read: the settings record lives in the *user's*
repo and could be edited by other tooling, so it returns `""` unless the value
`isinstance(..., str)`, then re-caps. Never trust a field just because you wrote
it once.

### 4. Templates — `write.html`, `document.html`, the record partial

- **`write.html`** — the composer form (title / optional description / markdown
  body), `{% csrf_token %}`, `noindex`, and a success state that links the
  permalink. Standard, but note the error branch re-shows the exact `body` so a
  rejected 30 kB draft survives.
- **`document.html`** — the `/posts/<rkey>` page. The core is the fallback:

  ```django
  {% if markdown_source %}{{ markdown_source|markdownify }}
  {% else %}{{ document.textContent|linebreaks }}{% endif %}
  ```

  A mosaic-authored doc renders its markdown through the **sanitizing**
  `markdownify` filter; a foreign doc with no mosaic block renders the safe
  plain-text `textContent`. Theme tokens are inlined as `:root{…}` (they're
  validated enums/colors), and `custom.css` is pulled in only via `<link>` when
  `has_custom_css`.
- **`lexicons/site.standard.document.html`** (in the `atproto` app's templates) —
  the record partial for the home page's Writing section. It links the title to
  `/posts/{{ record.rkey }}` **only when `documents_linked`** is set — which
  `tenant_home` sets to `True`. In preview mode / lexicon pages there is no local
  `/posts/` route, so it renders the title unlinked. One partial, two contexts,
  gated by a single flag.

### 5. `tests/test_hosted_composer.py` — how the pieces are pinned

Skim `TidTest.test_tid_shape_and_ordering` (shape + sort order under mocked
clocks), `PublishTest` (record shape, publication-created-when-missing, the three
validation errors, custom-domain preference), `DocumentHelpersTest` (markdown
extraction, cache hit *and* miss both call `xrpc_get` once), and `CustomCssTest`
(`text/css` + `nosniff`, empty-when-unset, size-cap/type-check, dashboard
round-trip). The network is fully mocked; assertions check **payloads and
headers**, not live responses — the same discipline as PR 1.

## Deep dive: minting a TID locally, precisely

```python
TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"

def generate_tid():
    value = (int(time.time() * 1_000_000) << 10) | secrets.randbelow(1024)
    chars = []
    for _ in range(13):
        chars.append(TID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))
```

**What a TID is.** A TID ("timestamp identifier") is a **64-bit unsigned
integer** rendered as **13 characters** of a *sortable* base32 alphabet. The
layout of the integer:

- **bit 63 (the top bit): always 0.** Reserved; keeps every TID positive and
  keeps the string 13 chars.
- **bits 62–10 (53 bits): microseconds since the UNIX epoch.**
- **bits 9–0 (10 bits): a random "clock identifier."** Its job is to break ties
  between two processes (or threads) that read the same microsecond off the same
  clock.

Trace the code against that. `int(time.time() * 1_000_000)` is microseconds since
the epoch (~`1.75e15` today, about 51 bits). `<< 10` shifts it up to make room
for the clock id; `| secrets.randbelow(1024)` fills those low 10 bits with
cryptographic randomness. Because today's microsecond count is ~51 bits, shifting
left 10 lands around bit 61 — comfortably under bit 63, so the **top-bit-zero
invariant holds for free** (it won't break until ~year 2255).

The encoding loop is a plain big-endian base32: peel the low 5 bits (`value &
0x1F`) thirteen times, then `reversed`. `13 × 5 = 65` bits, so the most
significant character only carries the (always-zero) 65th/64th bits — which is
why every TID starts inside the low half of the alphabet and the strings line up
in columns.

**Why the alphabet matters.** `234567abcdefghijklmnopqrstuvwxyz` is chosen so
**ASCII/lexicographic order equals numeric order** (digits sort before letters,
no ambiguous `0/1/8/9`). That's the entire point: since the integer's high bits
are the timestamp, sorting the *strings* sorts by time. "Newest first" is just
`ORDER BY rkey DESC` — no field parsing. This is why ATProto collections page
efficiently by rkey.

**Why a client may mint its own rkey — safely.** An rkey only has to be **unique
within one collection in one repo**, and the PDS is the authority on that:
`createRecord` at a colliding rkey is rejected, and `putRecord` at an existing
rkey is a deliberate overwrite. Nothing about correctness needs the *server* to
choose the key. The TID scheme lets independent, offline clients each generate
keys that (a) essentially never collide (microsecond + 10 random bits) and (b)
still globally sort by time — coordination-free. So mosaic mints the rkey up
front, which is what lets it set the record's own `path` to `/posts/<rkey>`
before the write ever happens. (Contrast `createRecord`, which can let the server
assign the rkey — you'd then have to read it back before you knew the permalink.)

**Monotonicity — the sharp edge.** The spec wants TIDs from a single repo to be
*monotonically increasing*: a new record should sort after every prior one. This
composer leans on wall-clock microseconds for that and **does not track the last
rkey it emitted.** Two consequences worth naming:

- Two publishes within the *same* microsecond would tie on the timestamp bits and
  be ordered only by the random clock id — which can go either way. Strict
  monotonicity would require remembering the previous TID and bumping (spin the
  clock id, or wait a microsecond) so the next one is always greater.
- A backwards wall-clock step (NTP correction) could in principle mint a TID that
  sorts *before* an earlier one.

For a **human clicking Publish**, neither matters — sub-microsecond double-submits
don't happen, and the blast radius of a mis-order is "two of my own posts sort
oddly." But this is exactly the kind of gap that bites a high-throughput,
machine-driven writer, and it's why production TID libraries keep a per-process
"last TID" and enforce `next > last`. Notice the tests *mock* two distinct
timestamps (`side_effect=[…000001, …002]`) rather than mint twice off the real
clock — the test asserts the *sortability property*, sidestepping the
same-microsecond case entirely.

> **Review question.** `generate_tid()` uses `secrets.randbelow(1024)` for the
> clock id, not a monotonic counter. Given the composer never stores the last
> emitted TID, construct the scenario where two records sort in the wrong order,
> and decide whether the fix belongs here or is unnecessary for a
> human-driven composer. *(Answer: two `publish()` calls whose `time.time()`
> reads land in the same microsecond — the tie breaks on random bits. For a
> single human it's unreachable; a batch importer would need a monotonic guard.)*

## Deep dive: serving user CSS without an injection vector

The custom-CSS tier is the "Tumblr/Bearblog" escape hatch — a free-form
stylesheet the tenant controls. Free-form user content that the browser must
*interpret* is a stored-XSS minefield, and the hardening here is entirely about
**denying the CSS any path to becoming markup or script.**

```python
def tenant_custom_css(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    ...
    css = site_settings.custom_css(site_settings.load(identity))
    response = HttpResponse(css, content_type="text/css")
    response["X-Content-Type-Options"] = "nosniff"
    return response
```

Three properties, each closing a specific hole:

1. **Standalone, never inlined.** The templates reference it only as
   `<link rel="stylesheet" href="/custom.css">`. It is *never* dropped into HTML
   as `<style>{{ custom_css|safe }}</style>`. Why it matters: inside a `<style>`
   element, the byte sequence `</style><script>…</script>` **terminates the style
   context** and the browser then parses live script — instant stored XSS.
   Served as its own response, there is **no HTML context to break out of**: the
   entire body is a stylesheet, delivered through a `<link>`, and an external
   stylesheet has no way to execute JavaScript in modern browsers (legacy IE
   `expression()` is long dead; `url(javascript:…)` doesn't run). Same-origin CSS
   can restyle the tenant's own page — that's the feature — but it cannot inject
   nodes or run code.

2. **`Content-Type: text/css`.** The response is *declared* a stylesheet, not
   guessed. This pairs with:

3. **`X-Content-Type-Options: nosniff`.** This forbids the browser from
   **MIME-sniffing** — ignoring the declared type and inferring one from the
   bytes. Without it, a payload that *starts* like HTML (`<html>…`) could be
   sniffed and rendered as a document, script and all. With `nosniff` the browser
   must honor `text/css`: it will treat the bytes strictly as a stylesheet and,
   under the stylesheet MIME check, will simply **refuse to apply** anything that
   isn't validly `text/css` rather than fall back to interpreting it as markup.
   Both the "sniff it as HTML" and "smuggle a script via a wrong type" paths are
   closed. (See the MDN and OWASP links above.)

Two more layers reinforce it:

- **Size + type validation** (`site_settings.custom_css`): capped at 20k and
  coerced to `""` unless it's actually a `str` — because the field lives in the
  user's repo and could be poked into a non-string by other tooling. Read-side
  validation, not just write-side.
- **The validated theme stays separate.** Theme tokens (colors/enums) *are*
  inlined as `:root{ … }` — but only because every one was validated against a
  strict allowlist back in PR 8. The theme exposes `--mosaic-accent`,
  `--mosaic-background`, `--mosaic-text`, `--mosaic-font`, `--mosaic-radius` as
  custom properties, so the free-form stylesheet can *use* those tokens without
  the free-form text ever entering the HTML. The trusted, structured config is
  inlined; the untrusted, free-form config is quarantined behind a `text/css`
  endpoint.

Contrast this with the **markdown** render path, which takes the *opposite* but
equally valid approach: markdown genuinely must become HTML (headings, links,
bold), so it can't be quarantined — instead it's run through `markdownify` with
`BLEACH: True` and a tag/attr **allowlist** (`a`, `img`, `code`, … / `href`,
`src`, `alt`, `title`, `class`). Allowlist, not denylist (OWASP's rule): unknown
tags, `<script>`, `<style>`, and `on*` handlers are stripped, so even a document
authored by a *hostile other app* renders as inert HTML. Same threat model —
untrusted, user-authored content — two containment strategies matched to whether
the output must be markup.

## Design decisions & "why not X"

- **Why mint the rkey client-side instead of letting `createRecord` assign one?**
  So the permalink `/posts/<rkey>` is known *before* the write and can be stored
  in the record's own `path`. A server-assigned rkey would force a read-back
  before you knew the URL. TIDs are collision-safe and self-sorting, so local
  minting is free.
- **Why `putRecord` at a known rkey, not `createRecord`?** You already chose the
  rkey, so a `PUT` at that key is the honest verb — idempotent, and it sets up a
  future edit path (re-`putRecord` the same rkey). `ensure_publication` uses the
  same move at rkey `self`.
- **Why write `textContent` *and* a private `content` block?** Interop in both
  directions. `textContent` is the universal fallback every standard.site app can
  render; the `blog.mosaic.content.markdown` block is the rich source for apps
  (mosaic itself) that understand it. Filling only one would either lose fidelity
  or make the doc opaque to the network.
- **Why serve CSS standalone rather than inline it?** Injection containment — a
  stylesheet response can't break out into markup or script (the deep dive). The
  cost is one extra request; the payoff is that the feature can't become
  stored XSS.
- **Why cap the body at 30 kB and CSS at 20k?** These records live in the user's
  repo; the PDS bounds record size, and unbounded free-form fields are an abuse
  vector. 30 kB keeps a v1 post comfortably inside one record (no blob-splitting
  yet — media uploads are explicitly deferred).
- **Why invalidate read caches on publish?** The home/preview pages cache the
  collection list and the per-collection record lists (from earlier PRs). Without
  dropping those keys, a freshly published post wouldn't appear until the TTL
  expired. `_invalidate_read_caches` deletes exactly the keys that would hide it.

## Exercises

1. **Decode a TID.** Mint one with `composer.generate_tid()`, map each character
   back through `TID_ALPHABET` to rebuild the 64-bit integer, drop the low 10
   bits, and confirm the remaining value is microseconds-since-epoch matching
   "now." Which 10 bits did you discard, and what are they for?
2. **Spot the monotonicity gap.** Without mocking the clock, call
   `generate_tid()` in a tight loop and check whether the results are strictly
   increasing. When would they *not* be? Compare to the record-key spec's
   "monotonically increasing" language and decide whether the composer needs a
   fix.
3. **Attempt the break-out.** Put `</style><script>alert(1)</script>` in the
   dashboard's Custom CSS box, save, and then (a) `view-source` the tenant home
   page and (b) fetch `/custom.css`. Where does the payload land, and why can't it
   run? Now imagine `document.html` had used `<style>{{ custom_css|safe }}</style>`
   — write the exact payload that *would* fire, and name the two response
   properties that prevent it in the real code.
4. **Prove the interop fallback.** Write a `site.standard.document` into a repo
   from a *different* tool, filling only `title` + `textContent` (no
   `blog.mosaic.content.markdown` block). Load `/posts/<rkey>`: which template
   branch renders? Add a mosaic content block and reload — what changes, and which
   function decided?
5. **Feel the nosniff check.** Locally, drop the `X-Content-Type-Options` header
   from `tenant_custom_css` and serve a `custom.css` whose body begins with
   `<html>…<script>…`. Reason through what a MIME-sniffing browser *could* do, and
   what the header forbids.

## Verify it yourself

```bash
git checkout learn/10-composer
python -m pytest tests/test_hosted_composer.py -q          # network fully mocked
git show 160ac4d -- src/django_mosaic/hosted/composer.py   # the write path
```

## Glossary

- **TID** — timestamp identifier; a 64-bit int (top bit 0, 53 bits microseconds,
  10 bits random clock id) rendered as 13 sortable base32 chars. Client-mintable.
- **rkey** — record key; the last path segment of an AT-URI. Here it's a TID and
  doubles as the post's URL slug.
- **`putRecord` vs `createRecord`** — PUT at a known rkey (idempotent upsert) vs
  create with a server- or client-assigned rkey (collides if the key exists).
- **`textContent`** — standard.site's app-neutral plain-text body; the universal
  fallback for readers that don't understand a document's `content` blocks.
- **`content` union / `$type`** — an open list of typed content blocks; extend it
  with your own namespaced `$type` and foreign apps ignore what they don't know.
- **`site.standard.publication` / `site.standard.document`** — the site singleton
  (rkey `self`) and the per-post records mosaic writes.
- **MIME sniffing / `nosniff`** — a browser guessing a response's type from its
  bytes; `X-Content-Type-Options: nosniff` forbids the guess and enforces the
  declared `Content-Type`.
- **Bleach allowlist** — sanitizing rendered HTML by keeping only an explicit set
  of tags/attributes and dropping everything else (the OWASP-recommended
  direction).
