# PR 4 — 0.2.0 craft (the interlude)

> **Stack:** 4/12 · **base:** `learn/03-lexicon-pages`
> **Commits:** `5915a4a…9801df8` (inclusive, 9 commits — v0.2.0) · **What it adds:** the hardening
> and tooling round that cut **v0.2.0** — a CI matrix, a state-only FK
> migration, pagination, media cleanup, a project scaffolder, bounded reaction
> latency, richer `standard.site` documents, and a vendored JS dep.

## The one-sentence version

This isn't a feature PR — it's the *craft* round: nine commits that make the
existing code trustworthy enough to tag a minor release, and the richest of
them turns a raw `IntegerField` into a real `ForeignKey` **without moving a
single byte of data** by separating the migration's Django-state change from
its (empty) database change.

## Learning objectives

**Python / Django**

- **State-only migrations** with `SeparateDatabaseAndState`: convert an
  `IntegerField` into a `ForeignKey` that reuses the *identical* column, so
  the ORM's picture of the model changes while the physical table does not.
- Optional dependency **"extras"** (`fabric` behind a `deploy` extra), the
  **lazy-import** pattern that keeps them optional at runtime, and tests that
  **skip cleanly** (`pytest.importorskip`) when the extra is absent.
- A **CI test matrix** (Python 3.12/3.13 × Django 5.2/6.0) and why you should
  actually *exercise* the compatibility your classifiers claim.
- **Console-script scaffolding** — a `project.scripts` entry point
  (`mosaic-admin init`) for bootstrapping that can't be a management command.
- **Vendoring a JS dependency** to delete a CDN supply-chain/privacy surface.
- **Pagination** (`Paginator.get_page`) and **orphaned-file cleanup** on delete
  (`post_delete` signal).

**ATProto** *(light this round)*

- **Bounding render-path latency** for network reads: a short
  `REACTIONS_TIMEOUT` separate from the publish `TIMEOUT`, and a cache-only
  `REACTIONS_BLOCKING=False` mode warmed out of band by `manage.py atproto
  warm`.
- Improving `site.standard.document` **fidelity**: a `coverImage` blob reused
  from the companion embed, and a size-guarded native markdown `content` block.

## Grounding: official docs

- `SeparateDatabaseAndState` —
  <https://docs.djangoproject.com/en/stable/ref/migration-operations/#separatedatabaseandstate>
- Migrations, generally —
  <https://docs.djangoproject.com/en/stable/topics/migrations/>
- Pagination —
  <https://docs.djangoproject.com/en/stable/topics/pagination/>
- Packaging extras (dependency specifiers) —
  <https://packaging.python.org/en/latest/specifications/dependency-specifiers/>,
  <https://peps.python.org/pep-0508/>
- Semantic Versioning — <https://semver.org/>
- `standard.site` (the document lexicon) — <https://standard.site/>
- ATProto overview — <https://atproto.com/guides/overview>

## Background: the model this PR implements

There's no new subsystem here, so the "model" is a release-engineering one.
v0.1.9 shipped a working blog and PRs 1–3 bolted on the ATProto bridge,
reactions, and lexicon pages. Before stamping **0.2.0**, someone did the
unglamorous pass every maturing package needs: prove the claimed compatibility
in CI, pay down a data-modelling shortcut, stop leaking reader IPs to a CDN,
and bound the one place a network read sits on the render path. Read this PR as
a checklist of what "ready to tag a minor version" actually costs — and, per
[semver](https://semver.org/), why *additive, backward-compatible* changes like
these are exactly a **minor** bump, not a patch and not a major.

The one shortcut worth understanding in depth is the data model. `Post` pins
which revision of itself is public via `published_version_id`. In 0.1.9 that
was a bare `IntegerField` holding a `reversion.Version` primary key — a foreign
key in spirit but not in the schema, so nothing stopped a *deleted* Version
from leaving a dangling id behind. This PR makes it a real FK. The trick is
doing so on a live table without a data migration.

## Guided tour of the diff (read in this order)

The commits group into six themes. Read them in this order rather than
one-file-at-a-time.

### 1. CI, lint/format/type config (`5915a4a`)

Start with `.github/workflows/ci.yml`. Two jobs:

- **`lint`** — `ruff check`, `black --check`, and `ty check … || true`. The
  `|| true` makes the type check **advisory** (it reports but never fails the
  build) "until annotations land." That's a deliberate ratchet: turn a tool on
  in report-only mode first, tighten it later.
- **`test`** — a **matrix**:

  ```yaml
  strategy:
    fail-fast: false
    matrix:
      python-version: ["3.12", "3.13"]
      django-version: ["5.2", "6.0"]
  ```

  Four cells, one per Python×Django combination. `fail-fast: false` means one
  red cell doesn't cancel the others — you want to see *all* the breakage, not
  the first. The step that makes it real is `uv pip install
  "django~=${{ matrix.django-version }}.0"`, which pins Django *after* the
  normal sync so each cell runs against the version it names.

> **Review question.** `pyproject.toml` already carried the classifiers
> `Framework :: Django :: 5.2`, `:: 6.0` and `Python :: 3.12`, `:: 3.13`.
> What did those lines *actually* guarantee before this commit? (Answer:
> nothing. Classifiers are metadata strings — advertising, not tests. The
> matrix is what turns "we claim 5.2 and 6.0" into "CI proves 5.2 and 6.0 on
> every push.")

The lint config in `pyproject.toml` is worth a look for the *choices*: `ruff`
selects `E,F,W,I,UP,B,DJ` (the Django-specific `DJ` rules included) but
`ignore`s `E501` (black owns line length), `B008` (function calls in defaults —
endemic in Django), and `DJ012` (member ordering — "cosmetic, not worth
churning working code"). Both `black` and `ruff` `extend-exclude` migrations —
you never hand-lint generated migration files. The commit also adds empty
`py.typed` markers to both packages (PEP 561: this tells type checkers the
package ships inline types), and fixes what the new lint surfaced —
`raise … from`, explicit `ModelForm.Meta.fields` instead of `"__all__"`, import
sorting.

### 2. The FK migration (`7f26918`) — the main event

This is the deep-dive commit; the full walk-through is below. In the tour,
notice the *shape*: the model's `published_version_id = IntegerField(...)`
becomes `published_version = ForeignKey("reversion.Version",
on_delete=SET_NULL, …)`, and the whole migration's `database_operations` list
is **empty**. The admin changes are the tell that this was designed to be
invisible — `exclude` flips from `published_version_id` to `published_version`,
`update_fields=["published_version_id"]` becomes `["published_version"]`, and a
new test asserts that deleting a pinned Version now nulls the pointer instead
of orphaning it.

### 3. Pagination + media cleanup (`580c1a6`)

`views.py` grows a shared helper:

```python
def _paginate(request, queryset):
    per_page = getattr(settings, "MOSAIC_PAGE_SIZE", 10)
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))
```

`get_page` (not `page`) is the robust call: it coerces junk or out-of-range
`?page=` values to a valid page instead of raising — see the
[pagination docs](https://docs.djangoproject.com/en/stable/topics/pagination/).
Each view now puts a `Page` under both `posts` (still iterable for the template
loop) and `page_obj` (drives the controls). Note the deliberate ordering bug
they *avoided* in `home`: tags are computed from `all_posts` (the full
namespace) **before** pagination, so the topic list doesn't shrink to whatever
happens to be on page 2. The `includes/pagination.html` partial renders nothing
when `num_pages <= 1` — a good default for a reusable app.

The second half is a `post_delete` receiver on `ContentImage`:

```python
@receiver(post_delete, sender=ContentImage)
def _delete_contentimage_files(sender, instance, **kwargs):
    for field in (instance.image, instance.thumb):
        if not field:
            continue
        try:
            field.delete(save=False)
        except Exception as e:  # noqa: BLE001 - deletion is best-effort
            logger.warning(...)
```

Deleting a *row* never deletes the *file* in Django — the storage backend is
decoupled from the ORM. Without this, every deleted image (directly or via
`Post` cascade) leaks a file on disk forever. `delete(save=False)` removes the
file without re-saving the row (the row is already going away), and the
`except` keeps cleanup best-effort — a missing file must not crash a delete.

### 4. ATProto latency + fidelity (`45fcfcd`, `46effe4`)

Two ATProto-flavoured commits, both about the render path. **Latency
bounding** (`45fcfcd`): reaction fetches now pass an explicit short
`REACTIONS_TIMEOUT` (default 3s) instead of the 15s publish `TIMEOUT`, and a
new `REACTIONS_BLOCKING` flag lets you flip the render path to **cache-only** —
covered in its own deep dive below. **Fidelity** (`46effe4`): `_upload_thumb`
becomes `_upload_cover`, uploading the featured thumbnail **once** and reusing
the blob ref for *both* the document's new `coverImage` and the companion
post's embed. And `build_document` gains an optional `content` block:

```python
def _content_block(post):
    markdown_source = post.published_content or ""
    if len(markdown_source.encode("utf-8")) > conf.get_setting("CONTENT_MAX_INLINE_BYTES"):
        return None
    return {"$type": conf.get_setting("CONTENT_NSID"), "markdown": markdown_source}
```

`site.standard.document` has an **open `content` union** — a list where each
entry is tagged by its own `$type` NSID, and consumers ignore types they don't
understand. mosaic drops its *source markdown* in under a mosaic-owned NSID
(`blog.mosaic.content.markdown`), so a future mosaic AppView or re-import
reconstructs the post exactly, while other AppViews fall back to the plain
`textContent` that every document carries. The `CONTENT_MAX_INLINE_BYTES` guard
(30 KB) keeps a huge post from blowing the PDS record-size limit and returning
a 413 — `textContent` still carries it regardless, so nothing is lost, only the
lossless copy is skipped.

### 5. The scaffolder (`bdff162`)

`mosaic-admin init [dir]` generates a complete runnable flat project. The key
packaging idea is *why it's a console script and not a management command*:

```toml
[project.scripts]
mosaic-admin = "django_mosaic.scaffold:main"
```

A management command needs a `settings.py` to *already exist* to run at all —
so it can't be the thing that *creates* your settings. Bootstrapping has to
live outside Django's runtime, as a plain entry point that packaging installs
onto `PATH`. `scaffold.main` is a bare `argparse` CLI (no Django imported),
emitting `manage.py`, `settings.py`, `urls.py`, `wsgi.py`/`asgi.py`, `.env`,
template overrides, and static/media roots — a tree that passes `manage.py
check` and `migrate` on the first try.

### 6. Vendoring + the optional-extra skip + release (`0a891e8`, `9801df8`, `b749ecf`)

`9801df8` swaps two `<link>`/`<script>` tags on the post page from
`cdn.jsdelivr.net/npm/glightbox@3` to `{% static
'mosaic/vendor/glightbox/…' %}`, shipping glightbox 3.3.1 (MIT, license
included) in-tree. Three wins, all real: a floating `@3` tag is a
**supply-chain** risk (whatever jsDelivr serves for `@3` tomorrow runs on your
readers' machines); every post page load **leaked reader IPs** to a third party
(and quietly defeated the draft page's `Referrer-Policy: no-referrer`); and
self-contained pages work **offline** — more indieweb. `0a891e8` is the
optional-extra test skip (deep-dived below). `b749ecf` finalizes the CHANGELOG,
documents the new settings, and cuts **0.2.0** — an additive, backward-
compatible release, i.e. a minor bump under [semver](https://semver.org/).

## Deep dive: the state-only FK migration, precisely

The problem: `published_version_id` is an `IntegerField` holding a
`reversion.Version` pk. It behaves like a foreign key but the database doesn't
know that, so there's no `ON DELETE` behaviour — delete the pinned Version and
`Post` keeps a **dangling id** pointing at a row that's gone. We want a real
`ForeignKey` with `on_delete=SET_NULL`. The catch: a naive
`makemigrations` would see "field `published_version_id` removed, field
`published_version` added" and try to **drop and re-add a column** — obliterating
every pinned pointer already stored.

The insight is that the *physical column is already correct*. Django names a
`ForeignKey`'s column `<name>_id`, so `ForeignKey("...Version")` named
`published_version` lives in a column called `published_version_id` — **the
exact column the `IntegerField` already used**, already full of Version pks.
Nothing about the data needs to move. Only Django's *idea* of the field must
change. That is precisely what
[`SeparateDatabaseAndState`](https://docs.djangoproject.com/en/stable/ref/migration-operations/#separatedatabaseandstate)
is for:

```python
migrations.SeparateDatabaseAndState(
    state_operations=[
        migrations.RemoveField(model_name="post", name="published_version_id"),
        migrations.AddField(
            model_name="post",
            name="published_version",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to="reversion.version",
            ),
        ),
    ],
    database_operations=[],   # <-- the whole point: touch nothing on disk
)
```

Two lists. `state_operations` updates the migration graph's **in-memory model
state** — after this migration, Django *believes* `Post` has a
`published_version` FK, and all later `makemigrations` diffs against that
belief. `database_operations` is what actually runs **SQL** — and it's
**empty**, so the table is untouched. The remove-then-add in the state list is
a no-op at the schema level because both map to the same column; you're just
telling Django "the field formerly known as this int is now this FK, same
column." Existing pinned pointers survive because they were never touched.
`SET_NULL` is now enforced by the ORM on Version deletion (Django emits the
`pre_delete`/collector logic), which the new regression test proves:

```python
def test_deleting_pinned_version_nulls_the_pointer(self):
    ...
    original.delete()
    self.draft_post.refresh_from_db()
    self.assertIsNone(self.draft_post.published_version_id)
```

Two supporting details make the swap seamless. First, the FK's raw-id accessor
`published_version_id` **still works everywhere it did before** — Django gives
every FK a `<name>_id` attribute for the raw pk — so downstream code reading
`.published_version_id` needs no change. Second, the admin: `PostAdminForm`
already edited this through a declared `published_version` *ChoiceField* (a
revision picker), so the commit `exclude`s the model FK from the generated form
(`exclude = ["published_version", ...]`) to avoid Django auto-building a second,
conflicting widget for it — the declared field survives the exclude and
`save_model` still reads it. And `@reversion.register(exclude=[...])` is updated
to the new field name so reversion doesn't try to serialize the FK into its own
history.

> **Review question.** Why is `database_operations=[]` correct here but would
> be a *bug* if the old field had been, say, a `CharField`? (Answer: because
> `IntegerField` and this `ForeignKey` compile to the *same column type and
> name*. The state/DB split only works when the DB truly needs no change; if
> the physical column differed, you'd need real `database_operations`
> (e.g. `AlterField`) to reconcile it. State-only is a scalpel for "the schema
> is already right, only Django's model is wrong.")

## Deep dive: the optional-extra pattern and bounded reads

**The `deploy` extra.** Deployment tooling pulls in `fabric` (and its
paramiko/cryptography chain) — heavy, and irrelevant to the 99% who just want a
blog. So it's an *extra*, not a hard dependency:

```toml
[project.optional-dependencies]
deploy = ["fabric>=3.2.2"]
```

Consumers who want it run `pip install django-mosaic[deploy]`; everyone else
never downloads fabric (see the
[dependency-specifier spec](https://packaging.python.org/en/latest/specifications/dependency-specifiers/)).
For this to hold at *runtime*, the code that needs fabric must not import it
until asked — `_deployment.py` imports `from fabric import Connection` at
module top, and that module is only imported when the `deployment` management
command actually runs, so a plain install never triggers it. The failure mode
this PR fixes is in **tests**: `test_deployment.py` imports `_deployment` at
collection time, which imports fabric at collection time — so without the extra
the *whole suite errored before running a single test*. The fix:

```python
import pytest
pytest.importorskip("fabric")

from django_mosaic.management.commands._deployment import (  # noqa: E402
    SHELL_SAFE_PATTERNS, DeploymentHandler,
)
```

`importorskip` runs at import time: if `fabric` isn't installed it **skips the
entire module** cleanly instead of erroring, and the `# noqa: E402` silences
the "import not at top of file" lint that the guard necessarily creates. The
suite now passes with *or* without the extra; CI installs `--all-extras` so the
deployment tests actually run there. This is the general pattern for optional
functionality: **make it optional to install, lazy to import, and skippable to
test.**

**Bounding the reaction read.** Reactions are the one ATProto read on the
public render path, and a slow or dead PDS/Constellation shouldn't hang a
reader's page. Two levers:

- A dedicated **short timeout**. `xrpc_get` grows a `timeout=` parameter so the
  render path can pass `REACTIONS_TIMEOUT` (3s) instead of the 15s publish
  `TIMEOUT`. Different call sites, different latency budgets — a background
  `manage.py atproto publish` can afford 15s; a reader page cannot.
- A **cache-only mode**. `fetch_thread`/`fetch_crossapp_counts` gain
  `blocking=`; when `False` they return only what's in cache and **never make a
  live call** (`if not blocking: return None`), so page latency added by
  reactions is **zero**. `reactions_for` defaults `blocking` to the
  `REACTIONS_BLOCKING` setting. You then keep the cache fresh *out of band*:

  ```python
  def _warm(self, post_id):
      for document in qs:
          cache.delete(f"mosaic_atproto:thread:{document.bsky_post_uri}")
          reactions.reactions_for(document.post, blocking=True)
  ```

  `manage.py atproto warm` (cron-friendly) does the blocking fetches; readers
  only ever hit warm cache. The companion `atproto check --post N` prints the
  *raw* `getPostThread`/Constellation JSON next to mosaic's parsed result, so
  the tolerant parsers can be validated against the real services (which the
  build sandbox's network policy can't reach). This is the
  cache-with-out-of-band-refresh pattern: move the network cost off the request
  and onto a schedule.

## Design decisions & "why not X"

- **Why `SeparateDatabaseAndState`, not a data migration?** Because no data
  moves — the column is already correct. A data migration would be slower,
  riskier, and pointless. Reach for state-only whenever the *table* is right
  and only Django's *model picture* is wrong (renames the DB already made,
  fields adopted from a legacy schema, an int that was always a pk).
- **Why an extra for fabric, not a hard dependency?** Runtime weight and blast
  radius. Most consumers never deploy with mosaic's tooling; making them
  download paramiko/cryptography anyway is rude and enlarges their supply
  chain. Extras + lazy import keep the cost on the people who opt in.
- **Why vendor glightbox instead of the CDN?** A pinned-in-tree copy is a known
  quantity; a floating `@3` CDN tag is code you don't control running on your
  readers' machines, plus an IP leak to a third party on every page. Vendoring
  trades a few KB in the repo for supply-chain and privacy control.
- **Why cache-only reactions instead of just a short timeout?** A 3s timeout
  still adds *up to 3s* on a cold cache. For a high-traffic site that's a
  tail-latency cliff on the first viewer of every post. Cache-only makes the
  render path's added latency provably zero and pushes the network cost to a
  cron job — at the cost of slightly staler counts, which is the right trade for
  reactions.
- **Why `get_page`, not `page`?** `page` raises on `?page=abc` or `?page=999`;
  `get_page` coerces both to something valid. On a public URL where the query
  string is attacker-controlled, never-raise is the correct default.

## Exercises

1. **Read the migration.** Open
   `src/django_mosaic/migrations/0014_remove_post_published_version_id_and_more.py`.
   Confirm `database_operations=[]`. Now run `sqlmigrate django_mosaic 0014` and
   verify it emits **no `ALTER TABLE`**. Why is that the proof the swap is
   state-only?
2. **Break it deliberately.** Regenerate the migration *without*
   `SeparateDatabaseAndState` (let `makemigrations` diff the two fields
   naively). What SQL does `sqlmigrate` show now, and what happens to an
   existing pinned `published_version_id` when it runs?
3. **Trace the extra.** In a fresh venv, `pip install django-mosaic` (no
   extra), then run `pytest tests/test_deployment.py`. Confirm it *skips*, not
   errors. Then `pip install 'django-mosaic[deploy]'` and confirm the same
   tests now *run*.
4. **Bound a read yourself.** Set `MOSAIC_ATPROTO["REACTIONS_BLOCKING"] =
   False`, load a synced post page (counts should be absent on a cold cache),
   run `manage.py atproto warm`, reload (counts appear) — all with zero live
   calls on the render path. Confirm with a breakpoint in `fetch_thread` that
   the `blocking=False` branch returns before any `xrpc_get`.
5. **Read the union.** Open the `site.standard.document` schema at
   <https://standard.site/> and check `build_document` against it. Is `content`
   an open union? What does an AppView that doesn't know
   `blog.mosaic.content.markdown` do with mosaic's block, and why is that safe?

## Verify it yourself

```bash
git checkout learn/04-hardening
python -m pytest -q                                   # full suite, all extras
python manage.py sqlmigrate django_mosaic 0014        # no ALTER TABLE -> state only
git show 7f26918 -- src/django_mosaic/migrations/     # the SeparateDatabaseAndState op
git show 45fcfcd -- src/django_mosaic/atproto/reactions.py   # the blocking= plumbing
```

## Glossary

- **`SeparateDatabaseAndState`** — a migration op splitting the change to
  Django's model *state* from the change to the *database*, so one can happen
  without the other (here: state changes, DB doesn't).
- **State operations / database operations** — the two lists inside it: the
  first mutates the migration graph's model picture; the second emits SQL.
- **Extra** — an optional, named dependency group (`package[extra]`) not
  installed by default.
- **`py.typed`** — a PEP 561 marker file declaring a package ships inline type
  annotations.
- **CI matrix** — a job replicated across a grid of versions (here Python ×
  Django) so each combination is tested.
- **Console script** — a `project.scripts` entry point packaging installs as an
  executable on `PATH`.
- **Vendoring** — shipping a third-party dependency's source in-tree instead of
  fetching it at build/run time.
- **`get_page`** — `Paginator` method that returns a valid page for any input,
  coercing junk/out-of-range values instead of raising.
- **coverImage / open `content` union** — `site.standard.document` fields: a
  blob-ref cover, and a `$type`-tagged list where consumers ignore block types
  they don't understand.
- **Cache-only (non-blocking) read** — serving only cached data on the request
  path and refreshing the cache out of band, so request latency stays bounded.
