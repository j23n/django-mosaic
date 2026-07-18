# PR 8 — Tenant dashboard: site config in the user's *own* PDS

> **Stack:** 8/12 · **base:** `learn/07-tenancy`
> **Commit:** `285ac57` · **What it adds:** a `/dashboard` where a signed-in
> tenant arranges their home page (sections show/hide, retitle, reorder) and
> picks a theme — and stores the **entire configuration as a
> `blog.mosaic.site.settings` record in their *own* repo**, never in mosaic's
> database.

## The one-sentence version

A tenant edits a no-JS form; on save, mosaic writes one record —
`blog.mosaic.site.settings`, rkey `self` — into **their** PDS via their OAuth
grant (PR 6), and reads it back over public XRPC to render the page; the
service database holds **zero** site config, so there is nothing to lock in.

## Learning objectives

**ATProto**

- Write your *app's* configuration as a **custom-lexicon record** into the
  **user's** repo instead of your own DB — the "no lock-in / users own their
  data" principle made concrete and mechanical.
- Define a **custom NSID** (`blog.mosaic.site.settings`) and a **singleton
  rkey** (`self`) — the two strings that *are* the contract.
- Round-trip a structured settings object: `putRecord` as the signed-in user
  (authenticated, DPoP-signed XRPC) vs. `getRecord` read back over
  **unauthenticated** public XRPC.
- Default gracefully when the record is **missing or malformed** — a config
  that lives in someone else's repo is data you don't control.

**Python / Django**

- **Validate untrusted design tokens** before they touch a stylesheet —
  injection-safe theming with a fixed vocabulary, never raw CSS.
- The `--mosaic-*` **CSS custom property** technique: emit validated values as
  custom properties, consume them with `var(--mosaic-*, fallback)`.
- Validate on **both write and read** — never trust stored data just because
  you wrote it once.
- Parsing a no-JS form (`getlist`, position fields) into an ordered structure;
  a cache with a **cached-miss sentinel**; degrading on network *and* protocol
  errors so a slow fetch can't take a page down.

## Grounding: official docs

Read these first; the code is a thin, opinionated layer over them.

- Data ownership ethos & mental model — <https://atproto.com/guides/overview>
- Data model (records as typed JSON) — <https://atproto.com/specs/data-model>
- Repository (collections, records, rkeys) —
  <https://atproto.com/specs/repository>
- Lexicon & NSID (defining your own record type) —
  <https://atproto.com/specs/lexicon>, <https://atproto.com/specs/nsid>
- ATProto OAuth (the auth behind the write) —
  <https://atproto.com/specs/oauth>
- CSS custom properties (MDN) —
  <https://developer.mozilla.org/en-US/docs/Web/CSS/Using_CSS_custom_properties>
- Django forms & validation —
  <https://docs.djangoproject.com/en/stable/ref/forms/validation/>

## Background: the model this PR implements

By PR 7 mosaic is multi-tenant: a `Tenant` row maps a subdomain to a DID, and
the middleware routes `alice.mosaic.example` to Alice's home, rendered by
reading **her** repo. But *how* her home looks — which collections show, in
what order, under what titles, in what theme — had nowhere to live except
mosaic's database. Putting it there would quietly re-introduce lock-in: Alice's
content is portable (it's in her PDS), but her *site* would be trapped in our
Postgres.

This PR closes that gap. The insight is that configuration is **just data**,
and ATProto already has a place for a user's data: their repo. So mosaic
defines a custom record type and writes the whole dashboard state there:

```
blog.mosaic.site.settings   (collection / NSID — mosaic's own)
  rkey: self                 (a singleton; one settings record per repo)
  value: { sections:[...], theme:{preset, tokens}, updatedAt }
```

The consequence is exact and worth stating plainly: **point any mosaic
instance at Alice's handle and it reproduces her site, configuration
included** — because the config *is* in her repo. mosaic the service becomes
near-stateless with respect to how sites look; the `Tenant` table is just a
subdomain→DID index, not a store of truth.

Two NSIDs are now in play and they mean different things:

- `site.standard.*` (PR 1) — an **open, shared** lexicon for portable
  documents; other AppViews understand it.
- `blog.mosaic.site.settings` — **mosaic's own** lexicon. Another AppView
  wouldn't know what to do with it, and that's fine: it's mosaic-specific
  config that *happens* to be stored in the user's repo rather than ours. You
  are allowed to define NSIDs under a name you control and write them into any
  repo you're authorized to write to. (See the NSID spec.)

## Guided tour of the diff (read in this order)

### 1. `hosted/site_settings.py` — the whole idea, 197 lines

Read this file top to bottom; the module docstring states the thesis. Four
concerns live here: the **vocabulary** (what a theme can even be), **I/O**
(`load`/`save`), **validation** (`clean_theme`/`css_variables`), and
**merging** (`default_sections`/`effective_sections`/`arrange`).

The constants at the top *are* the contract:

```python
SETTINGS_NSID = "blog.mosaic.site.settings"
SETTINGS_RKEY = "self"
CACHE_SECONDS = 300
```

`rkey = "self"` is the ATProto idiom for a **singleton** record — a collection
that holds exactly one record whose key is a fixed string (the same convention
`app.bsky.actor.profile` uses for a profile). It means "there is one settings
record per repo," and it makes read and write address the *same* record every
time without listing the collection first.

### 2. `save()` — writing config into the user's repo

```python
def save(oauth_session, sections, theme):
    from django_mosaic.atproto.oauth import flow
    record = {
        "$type": SETTINGS_NSID,
        "sections": sections,
        "theme": theme,
        "updatedAt": timezone.now().isoformat(timespec="seconds"),
    }
    flow.xrpc_call(
        oauth_session,
        "com.atproto.repo.putRecord",
        method="POST",
        json_body={
            "repo": oauth_session.did,
            "collection": SETTINGS_NSID,
            "rkey": SETTINGS_RKEY,
            "record": record,
        },
    )
    cache.delete(_cache_key(oauth_session.did))
    return record
```

Everything important is here:

- `flow.xrpc_call` is the **authenticated, DPoP-signed** client from PR 6. The
  write happens **as the tenant**, against **their** PDS (`session.pds_url`),
  authorized by **their** OAuth grant. mosaic never holds Alice's keys; it
  holds a token she granted, and the PDS enforces that a repo can only be
  written by its owner. This is the whole reason the "config in the user's
  repo" model is safe: writes are user-authorized, not service-privileged.
- `repo == oauth_session.did`. You write to a repo by **DID**, never by handle
  — the immutable identifier (a recurring theme; PR 12 hardens the places that
  still forget it).
- `putRecord` with a fixed `rkey="self"` is create-or-**overwrite**. The
  settings record keeps its identity (and AT-URI) across every save, exactly
  like the `(uri, cid, rkey)` reuse discipline from PR 1 — but here the rkey is
  a constant, so there's nothing to track locally.
- On success, **invalidate the read cache** so the tenant's next page load
  reflects the save instead of the stale 5-minute copy.

### 3. `load()` — reading it back, tolerantly

```python
def load(identity):
    cached = cache.get(_cache_key(identity.did))
    if cached is not None:
        return cached or None            # "" marks a cached miss
    try:
        data = xrpc_get(identity.pds_url, "com.atproto.repo.getRecord", {
            "repo": identity.did,
            "collection": SETTINGS_NSID,
            "rkey": SETTINGS_RKEY,
        })
        value = data.get("value") or None
    except (AtprotoError, requests.RequestException):
        value = None                     # not written yet, or PDS unreachable
    cache.set(_cache_key(identity.did), value or "", CACHE_SECONDS)
    return value
```

Three details reward attention:

- **Reads are public XRPC.** `xrpc_get` is unauthenticated — anyone can read
  anyone's public records, so rendering a tenant's home needs no session. This
  is the ATProto read/write asymmetry from the primer: reading is open, writing
  is authorized.
- **The cached-miss sentinel.** A brand-new tenant has *no* settings record, so
  `getRecord` raises. Caching `None` directly wouldn't work — `cache.get`
  returns `None` for "absent from cache" too, so you'd re-hit the PDS every
  page load for a record that doesn't exist. The fix: store `""` to mean "we
  looked, there's nothing," and translate `"" → None` on the way out. The
  `if cached is not None` / `cached or None` dance is precisely this
  three-state logic (cache-absent, cached-miss, cached-hit) compressed.
- **`except (AtprotoError, requests.RequestException)`.** Catching *both* the
  protocol error (record genuinely absent) **and** the transport error (PDS
  slow or down) means a struggling PDS degrades to "render with defaults,"
  never "500 the tenant's home page." Config that lives in someone else's
  infrastructure *must* be treated as best-effort.

### 4. `clean_theme()` — validate untrusted tokens (the security core)

A theme is a `preset` plus a bag of `tokens`. The tokens come from a form, or —
crucially — from a **record in the user's repo that anyone with write access
could have hand-edited**. So every value is validated against a fixed
vocabulary before it is allowed to exist:

```python
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
COLOR_TOKENS = ("accent", "background", "text")
FONT_CHOICES  = {"sans": "...sans-serif", "serif": "...serif", "mono": "...monospace"}
RADIUS_CHOICES = {"none": "0", "small": "4px", "large": "12px"}
```

```python
def clean_theme(preset, tokens):
    theme = {"preset": preset if preset in PRESETS else "plain", "tokens": {}}
    supplied = {k: v for k, v in (tokens or {}).items() if v}
    merged = {**PRESETS[theme["preset"]], **supplied}
    for name in COLOR_TOKENS:
        value = str(merged.get(name, "")).strip()
        if COLOR_RE.match(value):
            theme["tokens"][name] = value
    if merged.get("font") in FONT_CHOICES:
        theme["tokens"]["font"] = merged["font"]
    if merged.get("radius") in RADIUS_CHOICES:
        theme["tokens"]["radius"] = merged["radius"]
    return theme
```

Note the shape of the safety guarantee:

- **Colors are matched against a strict regex** — `#rgb` or `#rrggbb`, nothing
  else. `url(javascript:alert(1))` and `red;}body{display:none` both simply
  **fail to match and are dropped**. There is no escaping step to get wrong;
  the value either *is* a hex color or it never enters the dict.
- **Font and radius are enums.** The stored value is a *key* (`"mono"`), and
  only known keys survive. The key is mapped to an actual CSS string
  (`ui-monospace, ...`) that mosaic controls — the user never supplies the CSS,
  only the *choice*. An attacker's `font: "'; }"` isn't in `FONT_CHOICES`, so
  it's dropped.
- **Unknown preset → `plain`.** A junk `preset` can't KeyError `PRESETS[...]`
  because it's normalized first.

This is the whitelist-not-blacklist discipline the Django validation docs
preach: don't try to *sanitize* dangerous input, **only accept known-good
shapes** and discard everything else.

### 5. `css_variables()` — emit `--mosaic-*`, re-validating on read

```python
def css_variables(settings_value):
    theme = (settings_value or {}).get("theme") or {}
    tokens = clean_theme(theme.get("preset", "plain"), theme.get("tokens"))["tokens"]
    css = {}
    for name in COLOR_TOKENS:
        if name in tokens:
            css[f"--mosaic-{name}"] = tokens[name]
    if "font" in tokens:
        css["--mosaic-font"] = FONT_CHOICES[tokens["font"]]
    if "radius" in tokens:
        css["--mosaic-radius"] = RADIUS_CHOICES[tokens["radius"]]
    return "".join(f"{k}:{v};" for k, v in css.items())
```

The load-bearing line is the second one: **it calls `clean_theme` again on the
stored record.** The data was validated when it was *written*, but it lives in
the user's repo — they could edit the record directly with any ATProto client
and put whatever they like in `tokens`. So mosaic re-validates on the **read
path** too. "We wrote it once, so it's safe" is exactly the assumption an
attacker with write access to their own repo would exploit. The test
`test_css_variables_revalidates_stored_record` proves a hostile stored value
(`accent: "red;}body{display:none"`) never reaches the output.

The output is a flat `--mosaic-accent:#ff0000;--mosaic-font:...;` string.

### 6. `hosted/home.html` — consuming the custom properties

```django
{% if css_variables %}<style>:root { {{ css_variables|safe }} }</style>{% endif %}
...
body { background: var(--mosaic-background, inherit);
       color: var(--mosaic-text, inherit);
       font-family: var(--mosaic-font, inherit); }
.tenant-home a { color: var(--mosaic-accent, inherit); }
.tenant-home img, .tenant-home .lexicon-record { border-radius: var(--mosaic-radius, 0); }
```

Two things to internalize:

- **`|safe` is justified, and the template says why.** Normally emitting
  unescaped user-influenced content into a page is the classic injection bug.
  Here it's safe *only because* `css_variables` is built exclusively from
  validated tokens — hex colors and enum-mapped strings mosaic controls. The
  template carries a comment pointing at that guarantee. `|safe` without that
  upstream validation would be a hole; the two are a pair.
- **The `var(--mosaic-*, fallback)` fallbacks** mean an unset token gracefully
  inherits the default stylesheet. A tenant who configures only an accent color
  gets *just* that changed and everything else default — no half-broken theme.
  This is the CSS-custom-property superpower: one indirection point, styled
  from a `:root` block, with a built-in default per use site. (See the MDN
  custom-properties page.)

### 7. `effective_sections` / `arrange` — merge stored config with reality

`default_sections(identity)` lists the known collections actually **present in
the repo** (via `lexicons.describe_repo` + the `PREVIEW_COLLECTIONS` map).
`effective_sections` then layers the **stored** section config (order, titles,
enabled flags) on top, and **appends any known collection that showed up in the
repo after the record was written** — so a tenant who starts using a new kind
of content sees it appear automatically instead of silently missing. `arrange`
applies that config to the sections the preview layer actually built: reorder,
drop the disabled ones, retitle the rest. Note `effective_sections` also
defends against a malformed stored record — non-string collections and
duplicates are skipped, titles are truncated — the same "don't trust stored
data" reflex as the theme path.

### 8. `hosted/views.py` — the `dashboard` view

OAuth-gated and stateless with respect to config. The control flow:

1. `conf.enabled()` → else 404 (inert when hosting isn't configured — the PR 1
   fail-closed pattern).
2. No OAuth session → **redirect to login** with `?next=/dashboard`.
3. Session but no `Tenant` for that DID → **redirect to `/claim`**.
4. GET → render the form from `effective_sections` + `clean_theme(stored)`.
5. POST → `_parse_sections(request.POST)` + `clean_theme(form tokens)`, then
   `site_settings.save(session, ...)`. On `flow.OAuthError`, re-render the form
   with the error at **HTTP 502** (the failure is upstream — the tenant's PDS
   rejected the write — not a client error). On success, redirect to
   `?saved=1` (Post/Redirect/Get, so a refresh doesn't re-submit).

`_parse_sections` is a small but instructive form parser: it reads the parallel
`getlist("collection")` plus `position:<collection>` / `title:<collection>` /
`enabled:<collection>` fields, bounds every value (collection ≤ 200 chars,
title ≤ 100), and returns rows **sorted by (position, original index)** so a
tie in position falls back to form order deterministically. No JavaScript — the
whole reorder UI is `<input type="number">` position boxes.

### 9. `tests/test_hosted_dashboard.py` — what's asserted

Skim it for the discipline. `ThemeValidationTest` is the security spec:
`test_clean_theme_keeps_valid_drops_invalid` feeds in
`url(javascript:...)` and `comic-sans` and asserts they don't survive;
`test_css_variables_revalidates_stored_record` asserts a hostile *stored*
record can't smuggle CSS. `SettingsRecordTest` checks the round-trip
(`putRecord` payload shape, `self` rkey, cache invalidation) and the
cached-miss-once behavior (`get.assert_called_once()` across two `load`s). The
view and end-to-end tests confirm the redirects, the 502-on-write-failure, and
that a retitled/disabled section and a themed token actually change the
rendered `/` page.

## Deep dive: settings-as-a-PDS-record

**Why it matters philosophically.** ATProto's pitch (see the overview guide) is
credible exit: your identity and data are yours, and you can take them to
another host or app. It's easy to honor that for *content* while quietly
violating it for everything around the content — preferences, layout, theme,
the shape of your site. Those feel like "app settings," so they drift into the
app's database, and now leaving the app means rebuilding your site from
scratch. That's soft lock-in, and it's the default outcome unless you resist
it. This PR resists it by refusing to treat config as special: **config is data,
data goes in the user's repo.** The test of the principle isn't the exciting
data (posts) — it's the boring data (a theme color). mosaic passes that test.

**Why it matters mechanically.** Because the config is a record in Alice's repo:

- The service is **near-stateless** about presentation. The `Tenant` table maps
  subdomain→DID and nothing else about how the site looks; there is no
  migration to run when the theme vocabulary grows, no config table to back up.
- The config is **portable by construction**. A second AppView that understands
  `blog.mosaic.site.settings` (or mosaic itself, self-hosted) reads the same
  record and reproduces the same site. Even an AppView that *doesn't* recognize
  the NSID still leaves the record intact in the repo — nothing is lost.
- It composes with the **read cache** exactly like any other public record:
  `load` fetches over public XRPC, caches for 5 minutes, and PR 11's firehose
  will later invalidate that cache when the record changes.

**The cost, honestly.** Config now lives behind a network hop you don't
control, so every read must tolerate the PDS being slow, down, or the record
being absent or malformed — which is why `load` catches both error families,
`css_variables` re-validates, and `effective_sections` skips junk entries. You
trade a reliable local row for a portable remote one, and pay for it with
defensive reads. For a "users own their data" product, that trade is the point.

## Deep dive: safe design tokens → CSS custom properties

Letting users theme a page is letting untrusted input influence a stylesheet —
one of the sharper injection surfaces, because CSS can exfiltrate data, hide
content, and (via old vectors) worse. The naïve version — a free-form "custom
CSS" box, or string-formatting user colors straight into a `style` attribute —
is a vulnerability. This PR gets it right with three compounding moves:

1. **A closed vocabulary, not free-form CSS.** A theme is at most three hex
   colors and two enum choices. There is no syntax in which a user can express
   anything *but* those. The attack surface is the size of the vocabulary.
2. **Validate to the vocabulary, dropping the rest.** Colors must match
   `^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$`; font/radius must be **keys** in a
   map mosaic owns. The user picks a *choice*; mosaic supplies the *CSS*. There
   is no user-authored CSS anywhere in the pipeline, so there is nothing to
   escape and nothing to get wrong.
3. **Validate on write *and* read, then emit as custom properties.** The
   validated values become `--mosaic-*` custom properties in one `:root` block;
   the stylesheet consumes them with `var(--mosaic-name, fallback)`. Custom
   properties are the ideal delivery vehicle here: they're *values*, not
   arbitrary declarations — a `--mosaic-accent` can only be *used* where the
   stylesheet already decided to use it (`a { color: var(--mosaic-accent) }`),
   so even a malformed value can only break *that* property, not inject new
   rules. And the per-use fallback keeps a partial theme coherent.

The re-validation on read is the subtle, essential part. Because the tokens
live in the user's repo, the write-time check is not a security boundary you can
rely on — the user can edit the record out-of-band. `css_variables` calling
`clean_theme` again makes the **render path** the real boundary. When you store
config somewhere the user can edit directly, validate where you *consume* it,
not (only) where you *write* it.

## Design decisions & "why not X"

- **Why store config in the user's repo instead of our DB?** No lock-in, and a
  near-stateless service (see the first deep dive). The cost is defensive reads;
  for this product it's worth it.
- **Why a fixed token vocabulary instead of a custom-CSS box?** Safety and
  simplicity now; the setup doc calls custom CSS "a later, deliberate tier."
  You can always widen a whitelist; you can't easily un-ship an injection hole.
- **Why re-validate on read when we validated on write?** The record is in the
  user's repo; they can edit it directly. The write-time check is a convenience,
  not a boundary. The consume-time check is the boundary.
- **Why `rkey="self"` (a singleton) instead of a TID?** There is exactly one
  settings record per site; a fixed rkey lets read and write address it without
  a lookup, and `putRecord` overwrites in place. TIDs are for collections with
  many time-ordered records (posts), not for a singleton.
- **Why 502 on a save failure, not 500 or 400?** The tenant's request was fine
  and mosaic's code was fine; the failure was an **upstream** PDS write. 502
  ("bad gateway") names that precisely, and re-rendering the form preserves the
  tenant's unsaved edits.
- **Why a cached-miss sentinel (`""`) instead of caching `None`?** `cache.get`
  already returns `None` for "not in cache," so caching `None` is
  indistinguishable from a cache miss and would re-hit the PDS every load for a
  tenant who has no settings record yet.

## Exercises

1. **Trace the trust boundary.** Follow a single `accent` value from the
   `<input type="color">` in `dashboard.html` to the `body a { color: ... }`
   rule on the rendered home page. List every point it is validated. Which one
   is the *security* boundary, and why are the others merely convenience?
2. **Break it (then confirm it holds).** In a scratch project, hand-write a
   `blog.mosaic.site.settings` record with
   `theme.tokens.accent = "red;}body{display:none;}"` directly in a PDS, then
   load the tenant's home. Confirm the malicious value is dropped and the page
   renders. Which function saved you?
3. **Add a token.** Add a `weight` enum (`normal`/`bold`) to the vocabulary:
   extend `FONT_CHOICES`-style validation in `clean_theme`, emit
   `--mosaic-weight` in `css_variables`, consume it in `home.html`, and add the
   `<select>` to the dashboard. Note everything you must touch — the vocabulary
   is deliberately not open, so growth is explicit.
4. **Predict the cache bug.** Alice saves a new theme; `save` deletes her cache
   key. But the *tenant home* for `alice.mosaic.example` may be served by a
   different process with a separate local cache. When does she see her change,
   and what does PR 11 (Jetstream) do to close that window?
5. **Read the record.** After configuring a dashboard, view the raw record at
   `https://pdsls.dev/at://<your-handle>/blog.mosaic.site.settings/self`.
   Confirm your config really is in *your* repo, and that its `$type` and rkey
   match the constants in `site_settings.py`.

## Verify it yourself

```bash
git checkout learn/08-dashboard
python -m pytest tests/test_hosted_dashboard.py -q       # network fully mocked
git show 285ac57 -- src/django_mosaic/hosted/site_settings.py   # the whole idea
```

## Glossary

- **Custom NSID** — a record type name under a namespace you control
  (`blog.mosaic.site.settings`); other AppViews needn't understand it.
- **Singleton record** — a collection holding one record at a fixed rkey
  (`self`); read and write address the same record without a lookup.
- **`putRecord`** — create-or-overwrite a record at a known rkey, as the
  authenticated repo owner.
- **Design token** — a single validated presentation value (a color, a font
  choice) drawn from a closed vocabulary, never raw CSS.
- **CSS custom property** — a `--name: value` variable set in `:root` and read
  with `var(--name, fallback)`; a value, not an arbitrary declaration.
- **Cached-miss sentinel** — a placeholder (here `""`) cached to distinguish
  "we looked and found nothing" from "not in the cache."
- **Post/Redirect/Get** — reply to a successful POST with a redirect so a
  refresh doesn't re-submit the form.
