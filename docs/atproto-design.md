# Mosaic on ATProto — Design Notes

*Status: exploration / design discussion. Nothing here is implemented.*
*Research snapshot: 2026-07-03. The protocol is moving fast — re-verify before building.*

## The core reframe

Mosaic today is: Django DB as source of truth → templates render HTML → IndieWeb
markup (h-card, rel-me, feeds) makes it legible to the outside world.

Mosaic on ATProto becomes: **your PDS repo is the canonical store for published
public content; mosaic is your personal AppView and publishing client.** The
Django DB doesn't go away — it holds drafts, private content, and a cache/index
of records — but published public posts live as records in your repo at
`at://yourdomain.com/site.standard.document/<rkey>`, where every other app in
the ATmosphere can see, index, link, and reply to them.

This is the same POSSE instinct mosaic already has (own your content, syndicate
out), except the "own" layer itself becomes interoperable. IndieWeb markup and
ATProto records are complementary, not competing — keep both.

## Concept mapping

| Mosaic today | ATProto equivalent |
|---|---|
| `Post` (published, public ns) | `site.standard.document` record |
| The site itself | `site.standard.publication` record |
| `ContentImage` | blobs (`com.atproto.repo.uploadBlob`), `coverImage` on the document |
| `Tag` | `tags[]` array on the document |
| `Author` h-card / rel-me | DID + handle (your domain), profile records |
| `Namespace("public")` | the publication |
| `Namespace("private")` + magic-link auth | **stays off-protocol** (see § Private content) |
| Drafts + `secret_id` links + django-reversion | **stays local** — standard.site has no draft concept; a record's existence = published |
| RSS/Atom feed | keep it; plus `site.standard.graph.subscription` lets ATProto users follow the publication |
| Comments (none today) | Bluesky reply thread via `bskyPostRef` + cross-app comment records |

## standard.site in one page

The lexicons (namespace `site.standard.*`, by Leaflet/pckt/Offprint, now the
de-facto long-form standard — WordPress plugin, EmDash, Sequoia, Astro/Obsidian
integrations all emit it; Bluesky natively renders rich article cards from it
since ~June 2026):

- **`site.standard.publication`** — `url` (base URL), `name`, `description`,
  `icon` blob, `basicTheme`, and `preferences` (display consent toggles:
  `showInDiscover`, `showComments`, `showMentions`, `showRecommends`, …).
- **`site.standard.document`** — required `site` (AT-URI of the publication, or
  a plain https URL), `title`, `publishedAt`. Plus `path` (canonical URL =
  publication `url` + `path`), `description`, `tags[]`, `coverImage` (≤1 MB),
  `updatedAt`, `contributors[]`, and two fields that matter a lot:
  - **`textContent`** — plain-text rendition of the full content. The interop
    lowest common denominator: Bluesky computes reading time from it, indexers
    search it. Mosaic has markdown, so this is `bleach`-stripped rendered
    markdown — we already do exactly this for `summary`.
  - **`content`** — an *open union*. There is no standardized rich format.
    Leaflet puts its block format here; GreenGale points at its own markdown
    record. Mosaic's play: define a tiny `com.j23n.mosaic.post` (or similar)
    lexicon holding the raw markdown, reference it from `content`, and let
    `textContent` + the canonical URL carry interop.
  - **`bskyPostRef`** — strongRef to the companion `app.bsky.feed.post`. This
    is the comments hook (see below).
- **`site.standard.graph.subscription`** / **`site.standard.graph.recommend`** —
  cross-app follows and boosts of publications/documents.
- **Verification** is bidirectional: serve the publication's AT-URI at
  `https://<domain>/.well-known/site.standard.publication`, and each article
  page includes `<link rel="site.standard.document" href="at://…">`. Both are
  trivial Django views/template additions (must be server-rendered — Bluesky's
  crawler doesn't run JS — which mosaic already is).
- Canonical lexicon JSON:
  https://github.com/hyperlink-academy/leaflet/tree/main/lexicons/site/standard
- Updates are just `putRecord` on the same rkey with `updatedAt` set — which
  pairs neatly with mosaic's `published_version_id` pinning: "publish" pushes
  the pinned version to the PDS.

## Architecture: hybrid source of truth

*(See the next section for the leaner pure-AppView variant, which is probably
the better fit for a single-person site — this section kept for the tradeoff
analysis.)*

Two pure options, and the hybrid that actually makes sense:

- **A. Django-canonical, mirror to PDS on publish.** Like the WordPress plugin.
  Easiest migration, no new read paths. But other-app content (photos, repos)
  never appears, and edits made via other ATProto clients drift.
- **B. PDS-canonical, Django as pure AppView.** Maximally native, but ATProto
  has no drafts, no private data, and the PDS record size limits are real
  (Leaflet offloads large docs to blobs) — you'd be rebuilding mosaic's best
  features (drafts, versioning, secret links) on a substrate that doesn't
  support them.
- **C. Hybrid (recommended).**
  - *Authoring*: unchanged — Django admin, drafts, reversion, secret links all
    local. `is_published` transition triggers `putRecord` of the
    `site.standard.document` (+ blob uploads + companion Bluesky post).
    Store `at_uri`/`cid`/`bsky_post_uri` on `Post`.
  - *Aggregation*: a sync process ingests **your own DID's** records into a
    generic cache table, rendering component-per-lexicon. Backfill via
    `com.atproto.repo.describeRepo` + `listRecords`; stay live via Jetstream
    with `wantedDids=<your did>` (a websocket consumer or periodic
    `manage.py mosaic atproto sync` — cron-friendly, matching mosaic's
    low-ops posture).

Sketch of the new models:

```python
class AtIdentity(models.Model):      # singleton-ish
    handle = models.CharField(...)   # yourdomain.com
    did = models.CharField(...)
    pds_url = models.URLField(...)

class AtRecord(models.Model):        # generic cache of own-repo records
    did = models.CharField(...)
    collection = models.CharField(db_index=True)   # NSID
    rkey = models.CharField(...)
    cid = models.CharField(...)
    value = models.JSONField()
    indexed_at = models.DateTimeField(auto_now=True)
    class Meta:
        unique_together = ("did", "collection", "rkey")

# Post gains: at_uri, at_cid, bsky_post_uri (nullable)
```

Renderers are then a registry `{NSID: template/component}` — exactly the
pattern of tynanpurdy/at-home and flo-bit/blento, which do this today and are
worth reading.

## Leaner alternative: mosaic as a pure AppView

The hybrid above carries a hidden tax: a mapping/serde layer between Django
models and lexicon records, maintained per content type, in both directions.
For a single-person site none of that is necessary. The lean version:

**The PDS is the only source of truth. Mosaic renders records; it doesn't
model them.**

### No models, just templates

Records are JSON. Django templates read dicts natively
(`{{ record.value.title }}`), so the per-lexicon cost drops from
"model + serializer + migration" to **one template per NSID you care about**,
plus a generic fallback for everything else:

```
templates/lexicons/
  site.standard.document.html
  sh.tangled.repo.html
  social.grain.gallery.html
  _generic.html          # dump anything unknown, readably
```

A view resolves `collection` → template and passes the raw record through.
There is deliberately no validation layer — it's your own data, and
missing-key rendering degrades gracefully (an unknown/evolved field renders
as blank), which is *more* robust to lexicon evolution than typed models
that break on drift.

### No ingest pipeline either (at first)

Skip Jetstream, skip the cache table. Pages call the PDS's XRPC directly at
request time (`listRecords`, `getRecord`) and wrap results in Django's cache
framework with a short TTL. A personal site's traffic against a PDS on
localhost (or even bsky.social over the network) is nothing. Publishing busts
the cache — you know when you posted, because it's your site.

Two facts make request-time reads workable without any local index:

- **rkeys are TIDs** — timestamp-ordered — so `listRecords(reverse=true)` is
  reverse-chronological for free; a mixed home timeline is a k-way merge of
  a few sorted lists.
- Personal-scale data (hundreds to low thousands of records per collection)
  can be filtered in Python at request time. Tag pages don't need an index
  table until they measurably do — and if that day comes, add a single
  denormalized `(at_uri, tag)` table populated lazily, not a model layer.

### Authoring moves to the ecosystem (mostly)

A pure AppView doesn't write. Posts get authored in Leaflet or pckt (which
have drafts, stored off-PDS on their side), photos in Grain, repos in
Tangled — mosaic just shows the aggregate. This deletes the write path,
the blob upload code, and the versioning bridge entirely.

The pragmatic exception: if the mosaic admin authoring flow (markdown,
drafts, reversion, secret preview links) is worth keeping — and it's arguably
mosaic's soul — keep it for **one lexicon only**. Drafts stay local exactly
as today; the publish transition does a single `putRecord` of a
`site.standard.document` + companion Bluesky post. That's ~200 lines against
one schema, not a serde framework. Local `Post` rows for anything published
become disposable (the PDS copy is canonical; local drafts are the only
unrecoverable state).

What stays in Django either way: the private namespace + magic-link/OAuth
gating and draft secret links — those are off-protocol features by design
(see § Private content), not lexicon data.

### Self-hosting the PDS on the same box

Orthogonal decision, but attractive here:

- The reference PDS is a single low-maintenance container (SQLite per
  account + blob directory). Mosaic's deployment tooling
  (`manage.py mosaic deployment`) already provisions Docker + nginx +
  certbot on a VPS — adding a `pds` service and a `pds.example.com` vhost
  (with websocket upgrade for `subscribeRepos`) is squarely within its
  existing job. The hourly backup task covers the PDS data dir too.
- Handle stays `example.com` via `/.well-known/atproto-did` served by Django;
  the PDS itself lives on the subdomain.
- Same-box reads are loopback XRPC — effectively free, which is what makes
  the "no cache, no ingest" posture comfortable. (Use XRPC even locally;
  the PDS's SQLite layout is not a supported interface.)
- If you later want live inbound-interaction updates, your own PDS exposes
  `com.atproto.sync.subscribeRepos` directly — no relay or Jetstream
  dependency for your own writes.
- Costs to respect: you're now hosting your identity. Keep did:plc rotation
  keys offline, back up CAR exports, and make sure the relay crawls you
  (`requestCrawl`) so Bluesky/AppViews see your content. If that
  responsibility isn't appealing, the identical architecture works against a
  bsky.social-hosted PDS — "same machine" is an optimization, not a
  requirement. Decouple the two decisions; adopt the AppView architecture
  first, move the PDS later or never.

### What the lean architecture gives up

- Full-text search, tag indexes, and cross-collection queries need either
  request-time Python or small denormalized tables added on demand.
- The Django admin is no longer the editor for anything except the one kept
  lexicon (and private posts).
- Mosaic-the-reusable-package tension: a pure AppView assumes its users
  author in other ATProto apps. Fine for a personal deployment; it changes
  what `django-mosaic` *is* if shipped upstream. (A sibling app —
  `django_mosaic.atproto` or a separate package — that provides the
  record-view + template registry keeps the core CMS intact for non-ATProto
  users.)

## What else lives in a PDS repo (the aggregation payoff)

Everything below is fetchable with the same two calls (`describeRepo`,
`listRecords`) — a personal site becomes "render my whole repo":

- **Software projects** — Tangled (`sh.tangled.repo`, plus issues, pulls,
  `sh.tangled.feed.star` for stars, `sh.tangled.git.refUpdate` for activity).
- **Photos** — Grain (`social.grain.photo` + `.gallery` + `.gallery.item`,
  EXIF sidecars, `social.grain.favorite`); also Flashes (`blue.flashes.*`).
  A "photos" namespace page in mosaic could render Grain galleries directly.
- **Music** — teal.fm (`fm.teal.alpha.feed.play`) or Rocksky
  (`app.rocksky.scrobble`, `.actor.status` = now playing).
- **Books** — BookHive (`buzz.bookhive.book`: status/stars/review) or
  Skylights (`my.skylights.rel`, also movies/TV via Skywatched).
- **Events** — `community.lexicon.calendar.event` / `.rsvp` (Smoke Signal's
  lexicons, donated to the Lexicon Community).
- **Links** — Frontpage submissions (`fyi.unravel.frontpage.post`), Linkat
  board (`blue.linkat.board`), `community.lexicon.bookmarks.bookmark`.
- **Long-form elsewhere** — WhiteWind (`com.whtwnd.blog.entry`, markdown) if
  you ever wrote there; Leaflet (`pub.leaflet.document`).
- **Livestreams** — Streamplace (`place.stream.livestream`, chat lexicons).
- **Microblog** — your `app.bsky.feed.post`s, obviously.

Discovery aids: awesome-lexicons (lexicon-community), UFOs explorer
(ufos.microcosm.blue), pdsls.dev to browse any repo by hand, `@atcute/*` for
typed definitions of most of the above.

## Interactions: comments, reactions, mentions

The model: every interaction is a record *in the other person's repo* pointing
at your record's AT-URI (or your page's URL). Your repo only has your outbound
acts, so inbound requires either the Bluesky AppView or a backlink index.

1. **Comments (primary): Bluesky thread as comment section.** On publish,
   create a companion `app.bsky.feed.post` with an `app.bsky.embed.external`
   of the canonical URL; store its strongRef in the document's `bskyPostRef`.
   Render replies via unauthenticated
   `GET https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread?uri=…`
   — likes/repost counts come free on the same call. Server-side fetch + cache
   in Django fits mosaic better than the usual client-side JS widget (and
   respects the no-JS ethos). This is the established pattern (czue/
   bluesky-comments et al.), and it doubles as cross-posting: the companion
   post *is* your presence in Bluesky, and Bluesky upgrades the card with your
   publication metadata because of the standard.site records.
2. **Cross-app comments and reactions: Constellation.** The microcosm backlink
   index (`https://constellation.microcosm.blue`, self-hostable) indexes every
   AT-URI/DID/URL link in every record of every lexicon:
   - likes on anything: `/links/count?target=<at-uri>&collection=app.bsky.feed.like&path=.subject.uri`
   - Leaflet comments: `collection=pub.leaflet.comment&path=.subject`
   - recommends: `collection=site.standard.graph.recommend&path=.document`
   - stars on your Tangled repos, Grain favorites on your photos — same shape,
     because nearly every "like" lexicon is `{subject: strongRef, createdAt}`.
   - `/links/all?target=…` = a webmentions-endpoint analog for the ATmosphere.
   Hydrate the linking records via Slingshot (verified cached getRecord).
3. **Mentions**: Constellation on the page URL (it indexes plain URLs), or
   `app.bsky.feed.searchPosts?url=`.
4. **Real-time (optional)**: Spacedust (microcosm) streams link-creation events
   filtered by target account — "anyone interacted with anything of mine, in
   any lexicon" as one websocket. Nice-to-have; polling + cache is fine.

Render inbound content with the same care as any UGC (mosaic already bleaches;
treat comment text as untrusted plaintext, never HTML).

## Private content and "rolling my own encryption"

Research conclusion, stated bluntly: **do not publish ciphertext to the public
repo.** The reasons are structural, not implementation details:

- Everything in a repo transits the relay firehose and is mirrored/archived by
  third parties *forever*; deletion is best-effort and mostly not honored.
  Ciphertext published today is a harvest-now-decrypt-later liability with an
  infinite horizon. The Bluesky team has repeatedly discouraged exactly this
  (atproto discussions #121, #3363).
- Key rotation can't un-share what's archived; revoking a reader only protects
  *future* posts; granting history to a new reader (escrowed per-post DEKs)
  sacrifices forward secrecy.
- Metadata still leaks: collection NSIDs, rkeys (TIDs = timestamps), record
  counts/sizes, blob CIDs, your DID — enough for activity analysis even with
  perfect payload crypto.
- Nothing can validate/index opaque payloads, and blobs remain publicly
  fetchable by CID regardless.

Meanwhile the protocol is moving: **Proposal 0016 "Permissioned Data"** was
merged 2026-07-03 (bluesky-social/proposals #94) — spaces, per-space
permissioned repos on your own PDS, credential-gated sync, *no firehose*,
deliberately deniable/non-archival commits, OAuth `space:` scopes. It's the
Bluesky protocol team's main focus through summer 2026, and it's explicitly
**access control, not E2EE** (the PDS can read the data; E2EE may layer on top
later). Granular OAuth scopes + permission sets already shipped (proposal 0011).

So the recommended posture, which happens to be the officially recommended
pattern ("publish identities and public data through AT; store private data on
your own server") **and** what mosaic already does:

1. **Keep the private namespace off-protocol in Django.** It's already there,
   already gated, already has secret-link drafts. This isn't a compromise —
   it's currently the correct architecture, and Roomy, Bluesky's own bookmarks
   and DMs all do the same.
2. **Upgrade the gate from magic links to "Sign in with ATProto".** Add atproto
   OAuth login to the private namespace: reader authenticates as their DID
   (identity on-protocol), you maintain an allowlist of DIDs (or later, a
   Bluesky list/graph query). Content stays HTTPS-served from Django (content
   off-protocol). This gives real per-person access control with zero
   cryptographic liability, and it can coexist with magic links for
   non-ATProto readers.
3. **Optional: public stubs for discoverability.** Publish a minimal
   `com.j23n.mosaic.privatePost` record (or even a normal document whose
   canonical URL is gated) containing only "a private post exists at <URL>" —
   subscribers get notified through normal channels, the gate does the rest.
   Title optional; decide per-post how much metadata to leak.
4. **When permissioned data ships, migrate the private namespace onto spaces**
   rather than ever having rolled crypto. Track proposal 0016 and
   `com.atproto.simplespace`.

If experimenting with real encryption anyway (eyes open, for fun): per-post DEK
(XChaCha20-Poly1305), DEKs wrapped per-recipient with HPKE/age keys, ciphertext
as a blob + stub record in a custom lexicon, keys distributed out-of-band —
and accept every caveat above. Not recommended for anything you'd mind being
public in 15 years.

## Identity & plumbing

- **Domain as handle**: DNS TXT `_atproto.<domain>` or
  `/.well-known/atproto-did` (a one-line Django view). Site and identity share
  the domain; content addresses become `at://yourdomain.com/...`.
- **Resolution**: handle → DID (verify bidirectionally) → DID doc
  (plc.directory) → PDS endpoint. Slingshot's `resolveMiniDoc` does verified
  resolution in one call.
- **Writing**: single-user site → an app password via
  `com.atproto.server.createSession` is pragmatic; the proper path is atproto
  OAuth with granular scopes (`repo:site.standard.document`,
  `repo:app.bsky.feed.post`, `blob:image/*`). Python: `atproto` (MarshalX) SDK
  already ships the `site.standard.*` types.
- **Images**: upload as blobs, fetch via `com.atproto.sync.getBlob` or the
  Bluesky CDN (`cdn.bsky.app/img/...`); keep local copies as the render source
  — the PDS is a syndication target for media, not the CDN. 1 MB blob limits
  on `coverImage`/`icon` fit mosaic's existing JPEG pipeline (quality 90 at
  2048px may need a size backstop).

## Phased roadmap

Each phase is independently shippable and useful:

1. **Identity + comments (no data-model change).** Domain handle; well-known
   views; on publish, create companion Bluesky post (external embed); render
   its reply thread + like count on post pages via `getPostThread`, cached.
2. **standard.site publishing.** Publication record; document record on
   publish/update (pinned version → `putRecord`); `bskyPostRef`; verification
   link tags; blob upload for cover images. Result: posts are records, Bluesky
   shows rich article cards, Leaflet/Skyreader/etc. can render and subscribe.
3. **Own-repo aggregation.** `AtRecord` cache + `mosaic atproto sync`
   management command (backfill + poll, optionally Jetstream); renderer
   registry per NSID; new sections for projects/photos/music/books/events as
   desired.
4. **Cross-app interactions.** Constellation-backed recommends/likes/comment
   counts and cross-app comment rendering; optional Spacedust for live.
5. **Private namespace, ATProto-gated.** OAuth "Sign in with ATProto" +
   DID allowlist alongside magic links. Watch proposal 0016; adopt
   permissioned data when real.

## Open questions

- Content format in `document.content`: define a mosaic markdown lexicon
  (GreenGale-style), adopt Leaflet's block format, or ship `textContent`-only
  and let the canonical URL carry fidelity? (Start with the last; add a
  markdown lexicon when something can consume it.)
- Comment moderation: which sources render by default (Bluesky replies yes;
  arbitrary lexicons behind an allowlist?), and honoring the publication
  `preferences` toggles.
- Multi-author blogs (mosaic supports multiple `Author`s; a publication has
  one repo — `contributors[]` covers bylines, but each author cross-posting
  from their own DID is a design choice).
- Whether `django-mosaic` grows this in-tree or as a sibling package
  (`django-mosaic-atproto`) — the PyPI package is consumed by others, and the
  ATProto layer drags in httpx/websockets/SDK dependencies.

## Reference index

- standard.site lexicon JSON: github.com/hyperlink-academy/leaflet/tree/main/lexicons/site/standard
- Bluesky × standard.site: atproto.com/blog/standard-site-bluesky-timeline; discussion bluesky-social/atproto#4978
- Permissioned data: github.com/bluesky-social/proposals/blob/main/0016-permissioned-data/README.md; auth scopes: …/0011-auth-scopes/README.md
- Roadmaps: atproto.com/blog/2026-spring-roadmap, …/protocol-check-in-fall-2025
- Microcosm: constellation.microcosm.blue, slingshot.microcosm.blue, spacedust.microcosm.blue (source: github.com/at-microcosm)
- Jetstream: github.com/bluesky-social/jetstream
- Prior art for "render my whole repo": github.com/tynanpurdy/at-home, github.com/flo-bit/blento; conceptual: overreacted.io/a-social-filesystem
- Lexicon discovery: github.com/lexicon-community/awesome-lexicons, ufos.microcosm.blue, pdsls.dev
- Comments pattern: github.com/czue/bluesky-comments, natalie.sh/posts/bluesky-comments
- Encryption discussions: bluesky-social/atproto discussions #121, #3363; community E2EE WG: github.com/ATProtocol-Community/atmessaging-proto
