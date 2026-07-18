# PR 1 — The ATProto bridge (`django_mosaic.atproto`)

> **Stack:** 1/12 · **base:** `learn/base` (mosaic 0.1.9 + editor prep)
> **Commit:** `3a7e1ce` · **What it adds:** an opt-in Django sub-app that
> mirrors your published posts into your ATProto repo as
> `site.standard.document` records.

## The one-sentence version

You publish a blog post; a `post_save` signal, *after the transaction commits*,
opens an app-password XRPC session to your PDS and writes your post there as a
portable record — so your content lives in **your** repo, not just mosaic's
database.

## Learning objectives

**ATProto**

- Resolve identity end to end: **handle → DID → PDS** — and know which piece is
  stable and which is reassignable.
- Read a DID document and pull the PDS **service endpoint** out of it.
- Distinguish `did:plc` from `did:web` and how each resolves.
- Make XRPC calls by hand: `createSession`, `createRecord`, `putRecord`,
  `deleteRecord`, `uploadBlob`.
- Understand **records**, **collections/NSIDs**, **rkeys**, **AT-URIs**, and
  **blobs** as concrete JSON on the wire.
- See why "publish once, embed everywhere" uses a companion `app.bsky.feed.post`
  plus a `site.standard.document`.

**Python / Django**

- `transaction.on_commit` — why network side effects belong *after* commit, not
  in the signal body.
- Designing an **inert-until-configured** optional sub-app (`enabled()` gating).
- A settings-access shim (`conf.get_setting`) that keeps one `MOSAIC_ATPROTO`
  dict as the whole public surface.
- Tracking-model pattern: local rows that remember remote `(uri, cid, rkey)`.
- Testing network code with everything mocked (`unittest.mock`, monkeypatched
  `requests`).

## Grounding: official docs

Read these first; the code is a thin client over them.

- Overview & mental model — <https://atproto.com/guides/overview>
- Identity (DID + handle) — <https://atproto.com/specs/did>,
  <https://atproto.com/specs/handle>, `did:plc` method:
  <https://github.com/did-method-plc/did-method-plc>
- Repository, records, collections — <https://atproto.com/specs/repository>
- Record keys / TIDs — <https://atproto.com/specs/record-key>
- AT-URI scheme — <https://atproto.com/specs/at-uri-scheme>
- XRPC — <https://atproto.com/specs/xrpc>
- Blobs — <https://atproto.com/specs/blob>
- The `com.atproto.repo.*` and `com.atproto.identity.*` methods used here —
  <https://docs.bsky.app/docs/category/http-reference>
- `app.bsky.feed.post` + `app.bsky.embed.external` lexicons —
  <https://docs.bsky.app/docs/advanced-guides/posts>
- `site.standard.*` (the publication/document lexicons mosaic writes) —
  <https://standard.site/>

## Background: the model this PR implements

Your account is a **DID**. Your **handle** points at it and can change; the DID
never does. Your **PDS** hosts your **repository** — a signed store of
**records** in **collections** named by **NSID**. mosaic writes two collections:

- `site.standard.publication` — one record describing your site (a singleton).
- `site.standard.document` — one record per published post.

To reach your repo, mosaic must first answer *"where is it?"*. That is the
resolution chain, and it is the heart of `client.py`:

```
handle  --com.atproto.identity.resolveHandle-->  DID
DID     --plc.directory / did:web well-known-->  DID document
DID doc --service[type=AtprotoPersonalDataServer]-->  PDS URL
```

Once you have `(did, pds_url)`, everything else is HTTP `POST`s to
`{pds_url}/xrpc/<nsid>` with a bearer token.

## Guided tour of the diff (read in this order)

### 1. `atproto/conf.py` — the whole config surface

Start here. One `MOSAIC_ATPROTO` dict, a `DEFAULTS` map, and `get_setting()`.
The important idea is **`enabled()`**:

```python
def enabled():
    return bool(get_setting("HANDLE") and get_setting("APP_PASSWORD"))
```

Every entry point checks this. Installing `django_mosaic.atproto` but not
configuring it leaves the app *completely inert* — no signals fire, no network
is touched. This is the pattern for a reusable optional feature: **fail
closed to a no-op, never to an error.** Note the three NSID constants
(`DOCUMENT_NSID`, `PUBLICATION_NSID`, `BSKY_POST_NSID`) — those strings *are*
the protocol contract.

### 2. `atproto/client.py` — the XRPC client

This is the ATProto core. Read `resolve_identity()` line by line:

- If `DID` and `PDS_URL` are both configured, **skip the network entirely** —
  the override path for self-hosted/air-gapped setups. (A recurring ATProto
  design theme: nothing forces you through a central service.)
- Otherwise resolve the handle via `com.atproto.identity.resolveHandle` on
  `public.api.bsky.app`, then fetch the DID document:
  - `did:plc:…` → `GET https://plc.directory/{did}` (a verifiable directory of
    PLC operations; see the did-method-plc repo above).
  - `did:web:…` → `GET https://{domain}/.well-known/did.json` (the domain
    *is* the DID; no directory needed).
- Pull the PDS out of the doc's `service` array where
  `type == "AtprotoPersonalDataServer"`. That `serviceEndpoint` is your PDS.

Then `Session`:

- `Session.create()` calls `com.atproto.server.createSession` with your handle
  + **app password**, and keeps the returned `accessJwt`. (App passwords are the
  simple auth here; PR 6 replaces this with real OAuth.)
- `create_record` / `put_record` / `delete_record` are one-liners over
  `com.atproto.repo.*`. Notice the payload shape — `{repo, collection, record}`
  — that's the literal Lexicon input schema.
- `upload_blob` POSTs raw bytes with a `Content-Type`, and the PDS returns a
  **blob ref** (a CID + mimeType + size). You don't send image bytes inside a
  record; you upload the blob, get a ref, and embed the *ref*. (See the blob
  spec above.)
- `xrpc_get` is the unauthenticated read helper — used later for public reads.

> **Review question.** `_raise_for_error` treats any `status_code >= 400` as
> failure. What does it *not* guard against, that a later PR will have to?
> (Answer: redirects. `requests` **follows redirects by default**, so a PDS URL
> that 3xx-redirects to an internal address (`http://169.254.169.254/…`) is
> fetched silently — `_raise_for_error` never even sees the 3xx. PR 12's SSRF
> hardening sets `allow_redirects=False`, treats `300–399` as an error, and
> validates that the resolved IP is public. Keep this in mind as you read; it's
> a real hole here.)

### 3. `atproto/models.py` — tracking records

`PublicationRecord` (singleton) and `DocumentRecord` (one-to-one with `Post`)
remember the remote `uri`, `cid`, and `rkey`. **Why keep these locally?** Because
`put_record`/`delete_record` need the `rkey`, and updates must reuse the *same*
rkey so the record keeps its identity (and its AT-URI) instead of spawning a new
one each save. The local row is mosaic's memory of "what have I already written
to the repo, and where."

### 4. `atproto/publisher.py` — the orchestration

`publish_post()` is the whole flow. Trace it:

1. `ensure_publication(session)` — create-or-update the site record; returns its
   AT-URI (documents point at it via `site`).
2. Companion post: if there's no tracked bsky post yet and `COMPANION_POST` is
   on, upload the thumb blob and create an `app.bsky.feed.post` with an
   `app.bsky.embed.external` card (the canonical URL, title, description, thumb).
   This is what makes your article show up as a rich link in Bluesky.
3. `build_document()` assembles the `site.standard.document` — including `site`,
   `path`, `title`, `description`, `textContent`, `tags`, `publishedAt`, and a
   `bskyPostRef` linking the two. On update it adds `updatedAt`.
4. Create (first time) or `put_record` with the stored rkey (update). Persist the
   returned `(uri, cid)` back onto the tracking row.

`unpublish_post()` deletes the document record; `syncable()` decides whether a
post *should* be on the PDS at all (`enabled() and is_published and namespace in
NAMESPACES`).

> **Note the two-record design.** mosaic writes a *document* (the canonical,
> app-neutral article, `site.standard.document`) **and** a *Bluesky post* (the
> social artifact, `app.bsky.feed.post`). One is for the open-standard document
> graph; the other is for the Bluesky social graph. Same content, two audiences,
> cross-linked by `bskyPostRef`. This is idiomatic ATProto: write to the lexicon
> that fits each consumer.

### 5. `atproto/signals.py` — the trigger (the key Python lesson)

```python
@receiver(post_save, sender=Post, dispatch_uid="mosaic_atproto_autopublish")
def sync_post_on_save(sender, instance, **kwargs):
    if not conf.enabled() or not conf.get_setting("AUTO_PUBLISH"):
        return
    def _sync():
        try:
            ...
        except Exception as e:  # noqa: BLE001 - never break the admin save
            logger.error(...)
    transaction.on_commit(_sync)
```

Three deliberate choices, each worth internalizing:

- **`transaction.on_commit(_sync)`** — the network call runs only *after* the DB
  transaction commits. If it ran inline and the transaction later rolled back,
  you'd have written a record to the PDS for a post that doesn't exist locally.
  `on_commit` also means the slow HTTP call isn't holding a DB transaction open.
  This is *the* pattern for "do an external side effect when a row is saved."
- **Swallow all exceptions** (`except Exception … log`). A PDS outage must never
  500 the admin save. The catch is scoped to the sync closure, and
  `manage.py atproto publish` exists to re-sync anything missed.
- **`dispatch_uid`** — makes the receiver registration idempotent so it can't be
  double-connected (which would double-publish).

### 6. `atproto/views.py` + `urls.py` — well-known endpoints

Two tiny text endpoints:

- `/.well-known/atproto-did` returns your DID — this is the mechanism that lets
  **your own domain become your handle** (bidirectional: the domain claims the
  DID, the DID's doc claims the handle). See the handle spec's "DID document"
  and "well-known" resolution methods.
- `/.well-known/site.standard.publication` returns the publication AT-URI, for
  verification.

### 7. `tests/test_atproto_bridge.py` — how to test network code

Skim this to learn the discipline: `requests` is mocked so no real network is
touched, and assertions check the *XRPC payloads* (right NSID, right record
shape) rather than live responses. This is how you test a protocol client
deterministically.

## Deep dive: `transaction.on_commit`, precisely

Django signals fire *inside* the transaction that saved the row. Anything you do
in a `post_save` receiver — including a 15-second HTTP call — happens while that
transaction is open, and will be **rolled back with it** if a later step fails.
`transaction.on_commit(fn)` registers `fn` to run only if/when the current
transaction successfully commits (and runs it *immediately* if you're in
autocommit mode). For external side effects keyed on "this row is now durably
saved," it is almost always what you want. Docs:
<https://docs.djangoproject.com/en/stable/topics/db/transactions/#performing-actions-after-commit>.

## Design decisions & "why not X"

- **Why app passwords, not OAuth, here?** App passwords are a two-line
  `createSession` call — perfect for a single-owner bridge. OAuth (PR 6) is the
  right tool once you're acting *on behalf of many users*. Start simple.
- **Why track `(uri, cid, rkey)` locally instead of listing the repo each
  time?** One local lookup beats a network round-trip, and it lets updates reuse
  the rkey deterministically. The trade-off — local state can drift from the
  repo — is what `manage.py atproto status`/`publish` reconcile.
- **Why a companion post at all?** Without it, sharing your article on Bluesky
  yields a bare link. The `embed.external` card is the difference between a URL
  and a rich preview — and it's created once and reused on updates.

## Exercises

1. **Trace an AT-URI.** After a publish, `DocumentRecord.uri` looks like
   `at://did:plc:XXXX/site.standard.document/3k…`. Identify the three parts
   against the AT-URI spec. Which part is the rkey? Is it a TID?
2. **Predict a bug.** A user changes their handle on Bluesky. Nothing in this PR
   stores the handle→DID mapping durably; `resolve_identity` re-resolves each
   `Session.create()`. Where could a handle change cause a wrong-repo write, and
   how does keying on DID (PR 5+) fix it?
3. **Hands-on.** Set `MOSAIC_ATPROTO` with a real handle + app password in a
   scratch project, run `manage.py atproto status`, then publish a post and
   inspect the created records in your PDS (e.g. via
   `https://pdsls.dev/at://<your-handle>`). Confirm both the document and the
   companion post exist and cross-reference each other.
4. **Read the lexicon.** Open the `app.bsky.feed.post` schema on docs.bsky.app
   and check `build_document`/`_create_companion_post` against it. Which fields
   are required? Which does mosaic omit, and is that valid?

## Verify it yourself

```bash
git checkout learn/01-atproto-bridge
python -m pytest tests/test_atproto_bridge.py -q      # network fully mocked
git show 3a7e1ce -- src/django_mosaic/atproto/client.py   # the XRPC core
```

## Glossary

- **DID** — stable account identifier (`did:plc:…`, `did:web:…`).
- **PDS** — Personal Data Server; hosts your repo.
- **Record / Collection / NSID** — a JSON document / a typed set of them / the
  reverse-DNS name of that type (`site.standard.document`).
- **rkey** — record key; the last path segment of the AT-URI.
- **AT-URI** — `at://<did>/<collection>/<rkey>`.
- **Blob** — binary attachment; uploaded separately, referenced by CID.
- **CID** — content-addressed hash identifying a specific record/blob version.
- **App password** — a scoped credential for `createSession` auth.
