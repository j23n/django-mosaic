# django-mosaic × ATProto — a stacked-PR learning journey

This is a **guided course** disguised as a pull-request stack. It replays how
`django-mosaic` grew from a plain Django blog (v0.1.9) into an ATProto-native
personal AppView and, finally, a multi-tenant hosted service — one reviewable
PR at a time.

You already write Python well. The goal here is twofold:

1. **Understand ATProto deeply** — not "what's a DID" trivia, but the actual
   mechanics: how identity resolves, how records and blobs move over XRPC, how
   OAuth is bound to a key with DPoP, how the firehose works, and *why* the
   protocol is shaped the way it is.
2. **Sharpen your Python/Django** — each PR is chosen to also carry a
   non-obvious language or framework technique (state-only migrations,
   `transaction.on_commit`, `sync_to_async`, dependency injection over
   settings singletons, DPoP/JWT crypto, middleware-based multi-tenancy).

## How to take the course

Each PR is a branch stacked on the previous one. Review them **bottom-up, in
order** (1 → 12); each builds on the last.

- `learn/base` — the starting point: **django-mosaic 0.1.9** (the last released
  version before any ATProto work) plus two pre-ATProto prep commits folded in
  (a general code-review hardening pass and the martor editor migration, which
  the bridge builds on). If you want to see those, read commits `f3088b6` and
  `c0efc5e` on `main`.
- `learn/01-atproto-bridge` … `learn/12-adversarial-review` — the twelve
  lessons.

For each PR:

1. **Read the lesson first** — `docs/learning/NN-*.md`. It states the learning
   objectives, gives the ATProto background you need, then walks you through
   the diff in reading order with the "why" behind each piece.
2. **Review the diff** as if it were a real PR. The lesson calls out what to
   look for and poses review questions (answers included, but try first).
3. **Do the exercises** — a mix of "spot the bug", "predict the behavior", and
   hands-on changes.
4. **Merge** into your journey branch and move on.

The lessons are *dense on purpose*. Read with the code open in a second pane.

## The map

| PR | Lesson | ATProto concepts | Python / Django craft |
|----|--------|------------------|-----------------------|
| 1 | [ATProto bridge](01-atproto-bridge.md) | XRPC, DID/handle/PDS resolution, records, blobs, `did:plc` vs `did:web`, AT-URIs | app layering, `transaction.on_commit`, network-mocked tests |
| 2 | [Reactions & comments](02-reactions.md) | `getPostThread`, the Constellation backlink index, AppViews vs indexes | tiered caching, graceful degradation, tolerant parsers |
| 3 | [Lexicon pages](03-lexicon-pages.md) | Lexicon schemas, NSIDs, TID rkey ordering, personal AppView pattern | request-time rendering, a template registry, custom filters |
| 4 | [0.2.0 craft (interlude)](04-hardening.md) | *(light)* bounded render-path latency | **state-only FK migration**, optional extras, CI matrices, packaging |
| 5 | [De-singletonize + preview](05-preview.md) | reading *any* actor's repo, DID-scoped caches, SSRF & the PDS | dependency injection vs settings singletons, throttling |
| 6 | [OAuth client](06-oauth.md) | ATProto OAuth: PAR, PKCE, **DPoP**, `private_key_jwt`, JWKS, token rotation | the `cryptography`/`PyJWT` stack, row-locked refresh |
| 7 | [Tenant registry + routing](07-tenancy.md) | DID as immutable identity, handle vs DID | Host-header middleware, multi-tenancy |
| 8 | [Dashboard: settings-in-the-PDS](08-dashboard.md) | writing app config into the *user's* repo (no lock-in) | CSS custom properties, design-token validation |
| 9 | [Custom domains + reports](09-domains.md) | domain-as-handle, on-demand TLS, operational verification | anti-abuse: honeypots, throttles, staleness gates |
| 10 | [Composer + write path](10-composer.md) | minting TID rkeys locally, `com.atproto.repo.*`, sanitizing render | markdown sanitization, content-type hardening |
| 11 | [Jetstream firehose](11-jetstream.md) | the firehose, Jetstream, cursors, at-scale cache invalidation | `asyncio`, `sync_to_async`, reconnect/backoff |
| 12 | [Adversarial review](12-adversarial-review.md) | SSRF depth, handle-takeover, DID-scoping, `iss` validation | session fixation, URL namespacing, review as a discipline |

## A 10-minute ATProto primer (read once, before PR 1)

ATProto (the **A**uthenticated **T**ransfer **Proto**col) is the protocol
underneath Bluesky, designed so your identity and data are portable across
apps and hosts. Five ideas carry almost everything in this course:

- **Identity is a DID.** Every account has a stable **DID** (Decentralized
  IDentifier), e.g. `did:plc:ewvi7...`. It never changes. Your **handle**
  (`alice.bsky.social`) is just a human-friendly, *reassignable* pointer to
  the DID — which is exactly why later PRs are careful to key everything on the
  DID, not the handle. See <https://atproto.com/specs/did> and
  <https://atproto.com/specs/handle>.
- **Data lives in a repo on a PDS.** Your **PDS** (Personal Data Server) hosts
  your **repository**: a signed key-value store of **records**, grouped into
  **collections** named by **NSID** (e.g. `app.bsky.feed.post`). Each record
  has a **record key** (**rkey**); many are **TID**s — timestamp-ordered IDs,
  so "sort by rkey descending" means "newest first". See
  <https://atproto.com/specs/repository> and
  <https://atproto.com/specs/record-key>.
- **Everything is addressable by AT-URI.** `at://<did>/<collection>/<rkey>`
  uniquely names a record anywhere in the network.
  See <https://atproto.com/specs/at-uri-scheme>.
- **The wire protocol is XRPC.** Plain HTTP: `GET/POST` to
  `/xrpc/<nsid>` (e.g. `com.atproto.repo.createRecord`). Schemas for methods
  and records are declared in **Lexicon**.
  See <https://atproto.com/specs/xrpc> and <https://atproto.com/specs/lexicon>.
- **Reading is open; writing is authenticated.** Anyone can read public
  records; writing to a repo needs auth (an app password early on, full OAuth
  later). This asymmetry is why the "read side" (PRs 1–5) is simpler than the
  "write side" (PR 6+).

Official entry points worth bookmarking:
<https://atproto.com/guides/overview>, the lexicon reference at
<https://docs.bsky.app/docs/category/http-reference>, and the spec index at
<https://atproto.com/specs/atp>.

> Each lesson repeats the exact links you need for *that* PR under a
> **Grounding: official docs** heading, so you can always trace a claim back to
> the source.
