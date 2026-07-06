# Mosaic Hosted — Plan

*Status: design. Companion to `atproto-design.md` (see "Productization" there
for the phase A/B/C overview; this expands phase C).*

## The two decisions that make this tractable

Everything below follows from two constraints, chosen to keep a hosted mosaic
buildable by one person:

**1. Mosaic Hosted is not a website editor. It is a renderer with knobs.**
Customization is layered so that each layer is a form, not an editor:

| Layer | What the user does | What it is technically |
|---|---|---|
| Sections | Toggle/reorder/retitle collections (blog, photos, repos, books…) | Data. A list of `{collection, slug, title, enabled}` — the existing `LEXICON_PAGES` config behind a form. |
| Theme preset | Pick one of N curated themes | A named template pack + default tokens, shipped by us. |
| Design tokens | Colors, fonts, spacing, radius, light/dark — form controls with live preview | CSS custom properties injected into `:root`. |
| Custom CSS | A stylesheet textarea (CodeMirror), the escape hatch | One user stylesheet served after the theme's. The Tumblr / Bearblog / omg.lol model. |
| Templates / code | **Not offered in SaaS.** | Django templates are a code-execution surface; per-tenant template loading is an operational and security tarpit. Self-hosting is the full-control tier — that's the OSS product's job, and the honest upsell boundary. |

This ladder covers ~everyone: presets for the many, tokens for the fussy,
custom CSS for the tinkerers, self-host for developers. A drag-and-drop page
builder is explicitly out of scope — the section list plus per-collection
templates already determine layout, and blento owns the "arrange cards"
niche. We sell *coherent presentation of your whole ATmosphere identity*,
not a canvas.

**2. There is (almost) no non-ATProto content — including the site's own
configuration.** The user's content lives in their PDS; in hosted mode their
*site config* does too, as records in a mosaic lexicon (blento validates
this pattern):

- `…mosaic.site.settings` — theme preset id, design tokens, custom CSS,
  section list, about/bio blocks, nav links.
- Content: blog posts are `site.standard.document` (authored *in* the
  dashboard, written to their PDS via OAuth — mosaic hosted is a client,
  not a CMS); links are a Linkat board or nav entries in settings; photos/
  repos/books/scrobbles are whatever apps they already use.

Consequences, all good:
- **No lock-in, by construction.** A user's entire site — content *and*
  configuration — lives in their repo. `docker run mosaic` pointed at their
  handle reproduces their site. That's a marketing point ("your home is
  yours; we're just the landlord you can fire") and it's real.
- **The SaaS is nearly stateless.** Our Postgres holds: tenant registry
  (DID ↔ subdomain/custom domains), OAuth sessions, billing state, and
  caches. Losing our DB loses no user creativity. Backups, GDPR, and exports
  become trivial.
- "Add non-ATProto content in Django?" — **No.** The one exception worth
  considering is *drafts* (can't be public records); v1 answer: drafts live
  in browser localStorage + a "publish" button, or simply don't exist
  (write elsewhere, paste, publish). A server-side draft store is a later
  nicety, not a launch requirement. The private namespace stays out of
  hosted v1 entirely (deferred to permissioned data, per the main doc).

## Architecture

One Django monolith (the OSS engine + a `hosted` app), Postgres, Redis,
one worker process. No microservices.

### Tenancy & routing
- `Tenant(did, handle, subdomain, custom_domains[], plan, created_at, status)`.
- Host-header middleware: `alice.mosaic.example` or `alice.com` → tenant →
  request-scoped identity context. **Prerequisite refactor (M0):** the
  engine currently reads a singleton identity from `MOSAIC_ATPROTO`
  settings; every read path (`lexicons`, `reactions`, well-known views,
  templates) must take identity from a request context instead. This
  refactor also unlocks the OSS "preview any handle" mode — same code path.
- Caches are keyed by DID/URI already (mostly); audit and enforce.

### Identity & auth
- **Sign in with ATProto (OAuth)** is the account system — no passwords, no
  email signup. Claiming a site = proving control of the DID via OAuth.
  Scopes: `repo:` for the mosaic settings lexicon + `site.standard.*` +
  `app.bsky.feed.post` (publishing), transitional generic until granular
  scopes are universal.
- This is the gnarliest technical dependency (client metadata document,
  PAR, DPoP, token refresh, per-tenant sessions). Python support exists but
  is younger than the JS SDKs — budget 2–3 weeks alone for a solid OAuth
  client layer, and build it as a reusable module in the OSS package (self-
  hosters want "login with my PDS" too).

### Onboarding (the magic moment)
1. Landing page: "enter any handle" → instant read-only preview of their
   aggregated site (public data; no signup). This is the demo *and* the
   funnel.
2. "Claim this site" → OAuth → `describeRepo` → sections proposed from the
   collections they actually have → site live at `handle.mosaic.example` →
   dashboard.
3. Time-to-live-site target: under two minutes, zero configuration required.

### The dashboard (the only genuinely new surface)
Five screens, all forms — roughly two dozen views total:
- **Sections**: reorderable toggle list (proposed from their repo).
- **Theme**: preset gallery; token controls (color pickers, font select,
  scale slider) with a live preview iframe of their real site.
- **Custom CSS**: CodeMirror textarea (paid tier). Served on their site
  origin only; the dashboard itself never renders with user CSS. CSS-only
  (no HTML/JS injection), size-capped.
- **Write**: martor compose → publish to their PDS as `site.standard.document`
  + companion Bluesky post (the existing publisher, pointed at OAuth
  sessions instead of an app password).
- **Domain**: custom-domain wizard (DNS instructions, verification, auto
  TLS) and the bundled differentiator: **domain-as-handle** — we serve
  `/.well-known/atproto-did` on their domain and walk them through updating
  their handle. Nobody else productizes this.

### Data freshness
- Shared Jetstream consumer (one websocket, `wantedDids` = all tenant DIDs;
  the 10k-DID filter limit is many milestones away, then shard) →
  invalidate/refresh per-tenant caches on their writes.
- Reactions stay `REACTIONS_BLOCKING=False` + scheduled warming; Spacedust
  later for live inbound reactions.
- Edge/CDN caching per Host with short TTLs; blobs served via cdn.bsky.app
  wherever possible (bandwidth is the cost center to watch — avoid proxying
  images through our origin).

### Domains & TLS
- Wildcard cert for `*.mosaic.example`.
- Custom domains: Caddy on-demand TLS or a platform feature (Cloudflare for
  SaaS / Fly / Render). On-demand issuance keyed to the tenant registry.

### Billing, abuse, legal (the unglamorous 30%)
- Stripe. Free: subdomain + presets + tokens. Paid (~$5–8/mo): custom
  domain, domain-as-handle, custom CSS, faster freshness. The hosted margin
  funds the OSS.
- Abuse: we render user-selected public content under domains we serve —
  need ToS, a report→suspend flow, tenant status checks in middleware, and
  respect for Bluesky moderation labels on rendered content (at minimum
  hide `!hide`-labeled). Registration throttles. This is table stakes, not
  optional, and it's most of the difference between "weekend demo" and
  "product".

## Milestones (each independently shippable)

- **M0 — engine de-singletonization** (OSS, ~1-2 wk): identity from request
  context; "render any handle" read-only mode. Immediately useful as the
  OSS demo mode; hard prerequisite for everything else.
- **M1 — preview service** (~1 wk): deploy M0 publicly: `mosaic.example/
  @handle` previews. No accounts, no writes. Validates demand cheaply;
  the waitlist form goes here.
- **M2 — OAuth + claiming + dashboard core** (~4-6 wk): tenant registry,
  subdomain routing, sign-in-with-ATProto, sections + theme presets +
  tokens, settings stored as PDS records.
  - *M2a shipped:* ATProto OAuth client layer (`django_mosaic.atproto.oauth`
    behind the `oauth` extra) — confidential client, PAR, PKCE, DPoP,
    refresh, `OAuthSession` storage, `/oauth/*` routes. Needs live
    validation against bsky.social (built in a sandbox without egress).
  - *M2b shipped:* `django_mosaic.hosted` app — `Tenant` registry,
    `TenantMiddleware` Host routing (subdomain → tenant urlconf),
    OAuth-gated `/claim` flow, tenant home rendered from the PDS.
  - *M2c shipped:* dashboard core — `/dashboard` with section
    order/visibility/titles and theme presets + validated design tokens,
    all saved as a `blog.mosaic.site.settings` record in the tenant's own
    PDS (written via their OAuth grant, read back public + cached). M2 is
    feature-complete pending live OAuth validation.
- **M3 — domains + billing** (~3-4 wk): custom domains, on-demand TLS,
  domain-as-handle wizard, Stripe, ToS/report flow.
  - *Shipped:* custom domains end-to-end (dashboard connect flow,
    `/domains/check` on-demand TLS ask endpoint for Caddy, verification
    implicit in first served request), per-tenant
    `/.well-known/atproto-did` + dashboard domain-as-handle wizard, and
    the report → admin-suspend moderation flow.
  - *Remaining:* Stripe billing and paid-tier gating (needs real account
    keys/webhooks — deliberately left for a live environment), ToS/legal
    pages, moderation-label handling on rendered content.
- **M4 — write path** (~2-3 wk): dashboard composer publishing via OAuth;
  custom CSS tier; Jetstream shared ingest.
  - *Shipped:* `/dashboard/write` composer (TID-minted rkeys,
    publication ensured on first publish, documents written to the
    tenant's repo via OAuth), `/posts/<rkey>` document pages on tenant
    sites (sanitized markdown, textContent fallback), and the custom-CSS
    tier (stored in the settings record, served standalone at
    `/custom.css`).
  - *Remaining:* Jetstream shared ingest for cache invalidation, media
    uploads (blob) in the composer, editing/deleting published documents.

Realistic total: **3–4 months of focused solo work** to a billable v1, with
the risk concentrated in OAuth (M2) and the abuse/ops surface (M3). Not a
weekend, decisively not a website editor, and every milestone before M3
doubles as an improvement to the OSS engine.

## What could kill it / watch items
- blento expanding from cards to content feeds (their users, our concept).
- Leaflet expanding publications → whole-identity sites.
- OAuth scope granularity or PDS OAuth quirks eating M2 time.
- Bandwidth costs if image-heavy tenants can't ride cdn.bsky.app.
- Permissioned data shipping changes the private-content story — good for
  us (we planned for it), but expect users to ask for it immediately.
