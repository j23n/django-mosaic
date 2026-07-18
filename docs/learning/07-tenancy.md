# PR 7 — Tenant registry + Host routing + OAuth-gated claiming (`django_mosaic.hosted`)

> **Stack:** 7/12 · **base:** `learn/06-oauth`
> **Commit:** `3075a74` · **What it adds:** an opt-in Django sub-app that turns
> one mosaic instance into a multi-tenant host — a `Tenant` registry
> (**DID ↔ subdomain**), a Host-header routing middleware, and a `/claim` flow
> whose only proof of ownership is the OAuth grant from PR 6.

## The one-sentence version

A request for `alice.mosaic.example` is caught by a middleware that reads the
**Host header**, finds the `Tenant` row for `alice`, and swaps in a tiny
per-tenant URLconf whose one route renders Alice's site **live from her PDS** —
while `mosaic.example` itself passes straight through to your normal blog; and
the only way a row gets into that registry is a visitor proving, via an ATProto
OAuth sign-in, that they control the **DID** they're claiming a subdomain for.

## Learning objectives

**ATProto**

- Understand the **DID as the immutable identifier** a tenant is keyed on, and
  why keying on the **handle** would be a security bug — handles are
  reassignable pointers.
- See how **OAuth (PR 6)** supplies proof-of-ownership: you can only claim a
  site for the DID whose grant is in your session. No email, no password reset,
  no separate account system — the ATProto identity *is* the account.
- Render a full site with **zero local content**: profile header + a section per
  collection, read live from the tenant's repo through the PR 5 preview engine.
- Watch identity resolution (`handle → DID → PDS`) run once more, now on a
  *stored* handle, and spot where a stale handle can point at the wrong repo.

**Python / Django**

- Write a **request middleware** that dispatches on `request.get_host()` and
  swaps `request.urlconf` to route a whole host to a different URL map.
- The **per-tenant URLconf swap** as Django's supported multi-tenancy seam
  (`request.urlconf`), and why it beats prefixing every view with tenant logic.
- **Pass-through discipline**: a routing middleware must leave hosts it doesn't
  own completely untouched (base domain, admin, unrelated names).
- **Reserved-name + DNS-label slug validation**, a built-in-plus-configurable
  denylist, and a `RegexValidator` shared between model, form, and view.
- **Admin actions** (`@admin.action`) for `suspend`/`reactivate` moderation.
- Keeping an optional sub-app **inert until configured** and importable without
  its heavy extra (`oauth`) via a lazy import.

## Grounding: official docs

Read these first; the code is a thin layer over them.

- DID — the **immutable** account identifier — <https://atproto.com/specs/did>
- Handle — the **reassignable** human-friendly pointer —
  <https://atproto.com/specs/handle>
- Protocol overview & mental model — <https://atproto.com/guides/overview>
- ATProto OAuth (the proof that gates claiming) —
  <https://atproto.com/specs/oauth>
- Django middleware — <https://docs.djangoproject.com/en/stable/topics/http/middleware/>
- How Django processes a request / `request.urlconf` (the dynamic URLconf hook) —
  <https://docs.djangoproject.com/en/stable/topics/http/urls/#how-django-processes-a-request>
- Django admin actions —
  <https://docs.djangoproject.com/en/stable/ref/contrib/admin/actions/>

## Background: the model this PR implements

Everything so far served **one** account — the operator's, configured as
`MOSAIC_ATPROTO["HANDLE"]`. This PR is the seam where mosaic becomes a **host**:
one deployment, many personal sites, each on its own subdomain.

A tenant is described by almost nothing:

```
did       did:plc:alice          # the immutable key — this IS the tenant
handle    alice.example          # a convenience label, refreshed on claim
subdomain alice                  # alice.mosaic.example
status    active | suspended
```

That's the entire registry. **No content columns.** Alice's posts, books,
profile — all of it lives in *her* repo on *her* PDS, and the tenant home
fetches it at request time. This is the deliberate ATProto payoff: the row plus
the handle *reproduces the whole site*, because mosaic stores no canonical copy.
Suspend the row and the site goes dark; delete it and nothing of Alice's is
lost. The registry is an index into the ATmosphere, not a database of her work.

Two request paths now share one deployment:

```
mosaic.example         --(Host header)-->  your normal URLconf  (landing, /claim, admin)
alice.mosaic.example   --(Host header)-->  tenant_urls          (Alice's site, from her PDS)
```

The thing that decides which path a request takes is a **middleware reading the
Host header** — the whole PR turns on that one dispatch.

## Guided tour of the diff (read in this order)

### 1. `hosted/conf.py` — the config surface (and the inert switch)

Same shape as `atproto/conf.py` from PR 1: one `MOSAIC_HOSTED` dict, a
`DEFAULTS` map, `get_setting()`. The load-bearing line:

```python
def enabled():
    return bool(get_setting("BASE_DOMAIN"))
```

Install `django_mosaic.hosted` but leave `BASE_DOMAIN` unset and the app is a
no-op — the middleware passes every request through, `/claim` 404s. **Fail
closed to a no-op, never to an error**, exactly as PR 1's `enabled()` gated the
bridge. `base_domain()` normalizes (`.lower().strip(".")`) so comparisons are
canonical; `claim_open()` folds in `enabled()` so a kill-switch check can't
accidentally pass on an unconfigured install.

Note `BUILTIN_RESERVED` — a frozen set of infrastructure and confusable names
(`www`, `api`, `oauth`, `admin`, `mosaic`, …) that can *never* be claimed,
merged with the operator's `RESERVED_SUBDOMAINS`:

```python
def reserved_subdomains():
    return BUILTIN_RESERVED | {s.lower() for s in get_setting("RESERVED_SUBDOMAINS")}
```

The built-ins are not configurable-away on purpose: letting someone claim
`oauth.mosaic.example` or `admin.mosaic.example` would be a routing/impersonation
hazard regardless of operator config.

### 2. `hosted/models.py` — the thin registry (and the shared validator)

`Tenant` is the four fields above plus timestamps. Two things to internalize:

- **`did` is `unique=True`; `handle` is not.** The DID is the identity;
  the handle is a mutable label kept for display and for prefilling suggestions.
  One DID → one subdomain (`subdomain` is also unique). *Which* field is the key
  is the whole ATProto lesson of this PR — held for the deep dive below.
- **`subdomain_validator` is a module-level `RegexValidator`** reused three ways:
  on the model field, in the view's `_subdomain_error`, and (as the raw pattern)
  in the claim form's HTML `pattern=` attribute. One regex, `^[a-z0-9]([a-z0-9-]
  {0,61}[a-z0-9])?$` — DNS-label rules minus leading/trailing hyphens, capped at
  63. Defining validation once and importing it everywhere is why the model,
  server view, and client form can't drift out of agreement.

### 3. `hosted/middleware.py` — the Host-header router (the heart of the PR)

This is where you should spend your time; the deep dive dissects it further.

```python
class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        subdomain = self._subdomain(request)
        if subdomain:
            tenant = Tenant.objects.filter(subdomain=subdomain).first()
            if tenant is None:
                raise Http404(f"No site at {subdomain}.{conf.base_domain()}.")
            if tenant.status != Tenant.STATUS_ACTIVE:
                raise Http404("This site is unavailable.")
            request.tenant = tenant
            request.urlconf = "django_mosaic.hosted.tenant_urls"
        return self.get_response(request)
```

Read the invariants:

- **`request.tenant = None` is set unconditionally**, first thing. Every
  downstream view can now say `getattr(request, "tenant", None)` and trust it.
- **`request.urlconf` is only ever *set*, never cleared.** On a base-domain
  request the attribute is never touched, so Django falls back to `ROOT_URLCONF`.
  (The middleware test asserts `not hasattr(request, "urlconf")` on pass-through
  — that absence is the contract.)
- **Unknown and suspended tenants raise `Http404`** rather than passing through.
  A request that *is* under the base domain but names no live tenant is a dead
  end, not a fall-back to your blog. Suspension (set by the admin action) takes
  effect on the very next request — no cache to bust.

Now `_subdomain`, the parser that decides pass-through vs. route:

```python
@staticmethod
def _subdomain(request):
    if not conf.enabled():
        return None
    host = request.get_host().split(":", 1)[0].lower().strip(".")
    base = conf.base_domain()
    if host == base or not host.endswith("." + base):
        return None
    label = host[: -len(base) - 1]
    if "." in label:            # nested subdomains are not tenant hosts
        raise Http404("Unknown host.")
    return label
```

Four cases, and each matters:

1. **Not enabled** → `None` (pass through). The inert switch again.
2. **Host is the base domain, or not under it at all** → `None`. `mosaic.example`
   itself, `other.example`, an internal admin name — all untouched. The
   `endswith("." + base)` (note the dot) is what stops `notmosaic.example` from
   being mistaken for a subdomain of `mosaic.example`.
3. **Exactly one label under the base** → that's the tenant subdomain.
4. **Nested label** (`a.b.mosaic.example`) → `Http404`. A wildcard cert and DNS
   only cover one level; deeper names are rejected rather than silently treated
   as `a.b`.

`get_host()` is stripped of its port (`split(":", 1)`) and lowercased, so
`Alice.Mosaic.Example:8000` routes identically to `alice.mosaic.example`.

### 4. `hosted/tenant_urls.py` — the URLconf that gets swapped in

Thirteen lines, one route:

```python
urlpatterns = [
    path("", tenant_home, name="tenant-home"),
]
```

That is the *entire* URL space of a tenant host. When the middleware sets
`request.urlconf = "django_mosaic.hosted.tenant_urls"`, Django resolves this
request (and only this request) against this map instead of `ROOT_URLCONF`. So
`alice.mosaic.example/` hits `tenant_home` and `alice.mosaic.example/admin/`
404s — the tenant host has no admin, no `/claim`, no blog routes. Per-collection
pages and a dashboard slot in here in later milestones.

### 5. `hosted/views.py` — `tenant_home`: a site with no local content

```python
def tenant_home(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    try:
        identity = identity_mod.resolve(tenant.handle)
    except AtprotoError:
        logger.warning("Could not resolve tenant handle %s", tenant.handle)
        return render(request, "hosted/unavailable.html", status=503)
    profile = preview_mod.fetch_profile(identity)
    sections, other_collections = preview_mod.build_sections(identity)
    return render(request, "hosted/home.html", {...})
```

Trace it: resolve the stored handle to an `Identity` (`handle → DID → PDS`,
PR 1's chain), then reuse PR 5's preview engine — `fetch_profile` and
`build_sections` — to read the profile and a section per known collection
straight from the PDS. mosaic contributes *rendering*, not *content*.

Two deliberate differences from PR 5's preview view:

- **Indexable, no throttle.** PR 5's public preview carried `noindex` and a
  per-IP rate limit because it renders *arbitrary* actors on demand — an
  operator surface. A tenant home is the account's *own* site, so those come
  off. (`test_renders_indexable_home_from_pds` asserts the absence of both the
  `noindex` meta and the `X-Robots-Tag`.)
- **Degrade to 503, don't 500.** If the PDS can't be resolved, render
  `hosted/unavailable.html` with `status=503` — a transient-outage page, not a
  stack trace. A tenant's dead PDS is *their* server's problem; mosaic stays up.

> **Review question.** `tenant_home` re-checks `request.tenant is None` even
> though the middleware only routes here when a tenant is set. Why keep the
> guard? *(Answer: defense in depth — the view is importable and reversible
> independent of the middleware; if `tenant_urls` were ever included without the
> middleware in front, the guard 404s instead of `AttributeError`-ing. Views
> shouldn't trust that their middleware ran.)*

### 6. `hosted/views.py` — `claim`: OAuth is the whole auth system

```python
@require_http_methods(["GET", "POST"])
def claim(request):
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    flow = _oauth_flow()
    session = flow.current_session(request)
    login_url = f"{reverse('atproto-oauth-login')}?next={reverse('hosted-claim')}"
    if session is None:
        if request.method == "POST":
            return redirect(login_url)
        return render(request, "hosted/claim.html", {"login_url": login_url})

    existing = Tenant.objects.filter(did=session.did).first()
    ...
    tenant = Tenant.objects.create(
        did=session.did, handle=session.handle, subdomain=subdomain
    )
```

The crux — read it slowly — is that **the DID and handle written to the row come
from `session`, never from the request body.** `session` is the PR 6
`OAuthSession` resolved from the signed-in DID in `request.session`. A visitor
posts *only* a subdomain string; they cannot post a DID. So the site is created
for the DID they proved control of by signing in, and for no other. That single
data-flow choice is the entire ownership model:

- No `owner_email`, no password, no "verify you own this account" step — the
  OAuth grant already *is* that verification (see PR 6's PAR/PKCE/DPoP flow).
- `Tenant.objects.filter(did=session.did)` enforces **one subdomain per DID**:
  an already-claimed visitor is shown their existing site, and a POST to claim a
  second is redirected, not honored.
- The lazy `_oauth_flow()` import keeps `hosted` importable without the `oauth`
  extra installed — only the claim path pulls in `PyJWT`/`cryptography`.

`_subdomain_error` runs the ladder: non-empty → passes `subdomain_validator` →
not in `reserved_subdomains()` → not already taken. Each failure re-renders the
form with a message and the right status (`400` invalid, `403` claims closed).
`_suggest_subdomain` prefills a suggestion from the handle's first label, but
only if that suggestion would itself pass the ladder — so the form never
suggests a reserved or taken name.

> **Review question.** Input is `" Alice "`. What lands in the DB, and where is
> it normalized? *(Answer: `alice`. `request.POST.get("subdomain").strip()
> .lower()` in the view normalizes before validation; `test_signed_in_claims_
> subdomain` posts `" Alice "` and asserts `subdomain == "alice"`. Normalize at
> the boundary, validate the normalized value.)*

### 7. `hosted/admin.py` — moderation as admin actions

```python
@admin.action(description="Suspend selected tenants")
def suspend(self, request, queryset):
    queryset.update(status=Tenant.STATUS_SUSPENDED)
```

Two `@admin.action` methods, registered via `actions = ["suspend", "reactivate"]`.
Each is a single bulk `queryset.update()` — no per-row save, no signals, one SQL
`UPDATE`. Because the middleware checks `status` on *every* request, suspending
here stops the tenant's host from serving on the next hit. `did`/`created_at`/
`updated_at` are `readonly_fields` — the registry key is not hand-editable in the
admin. This is the canonical Django admin-actions pattern (see the actions doc).

### 8. Templates & migration

`hosted/home.html`, `claim.html`, `unavailable.html` are **standalone** — they do
*not* `extend` the operator's `base.html`, because a tenant site is not the
operator's blog and shouldn't inherit its chrome. All three are overridable by
shadowing `templates/hosted/*.html` in the deployment to restyle every tenant at
once. `claim.html` carries `robots: noindex` (a signup form, not content) while
`home.html` deliberately does not. `0001_initial.py` is an ordinary
`CreateModel` — note the `RegexValidator` is serialized *into* the migration,
so the DNS-label rule is enforced at the schema layer too.

## Deep dive: Host-header routing and the `request.urlconf` swap

Django resolves URLs by importing the module named in `settings.ROOT_URLCONF`
and matching `request.path` against its `urlpatterns`. The
[request-processing docs](https://docs.djangoproject.com/en/stable/topics/http/urls/#how-django-processes-a-request)
spell out the one hook this PR exploits:

> Django determines the root URLconf module to use. Ordinarily, this is the
> value of the `ROOT_URLCONF` setting, but if the incoming `HttpRequest` object
> has a `urlconf` attribute (set by middleware), its value will be used in place
> of the `ROOT_URLCONF` setting.

So a middleware that sets `request.urlconf` **redefines the entire URL space for
that one request.** That is the supported, per-request multi-tenancy seam — no
monkeypatching, no thread-locals. `TenantMiddleware` uses it to say: *for this
host, the site is `tenant_urls` (one page from a PDS); for every other host,
leave `ROOT_URLCONF` alone.*

Ordering matters. The middleware runs **top-down on the way in**
([middleware docs](https://docs.djangoproject.com/en/stable/topics/http/middleware/)),
and `request.urlconf` must be set *before* the `URLResolver` runs — i.e. before
the view is resolved, which is after all middleware `__call__` pre-`get_response`
code. Place `TenantMiddleware` after `CommonMiddleware` (so host normalization
and `ALLOWED_HOSTS` checks have happened) and before anything that reverses URLs.
And `ALLOWED_HOSTS` must include the leading-dot wildcard `".mosaic.example"`, or
Django rejects the subdomain host with `DisallowedHost` before your middleware
ever sees it.

Why a URLconf swap rather than one view that branches on `request.tenant`? Two
reasons. First, **isolation**: a tenant host genuinely has a *different* set of
valid URLs (no `/admin`, no `/claim`) — expressing that as a separate URL map is
honest, and it means a tenant can never reach an operator route by guessing a
path. Second, **growth**: later milestones add per-collection pages and a tenant
dashboard; they become routes in `tenant_urls`, and every one is automatically
scoped to the tenant host without touching the base-domain URLconf. The swap
turns "which site is this?" into a routing fact, decided once, at the edge.

The subtle correctness detail is the **pass-through guarantee**. A routing
middleware that owns some hosts is dangerous precisely on the hosts it *doesn't*
own: get the boundary wrong and you either hijack `mosaic.example` or leak tenant
routing onto unrelated names. `_subdomain` is written to return `None` (touch
nothing) on every case except "exactly one label under the base domain," and the
tests pin all of them — base domain, unrelated host, disabled app, nested label.
When you write host-dispatching middleware, the pass-through cases deserve *more*
test attention than the happy path.

## Deep dive: why tenants are keyed on DID, not handle

This is the ATProto heart of the PR, and it connects backward and forward
through the whole course.

A **handle** (`alice.example`) is a *reassignable pointer*. The
[handle spec](https://atproto.com/specs/handle) is explicit that a handle is
verified against the DID document and can be changed, transferred, or — if it's
a domain that expires — **taken over by someone else**. A **DID**
(`did:plc:alice`) is, per the [DID spec](https://atproto.com/specs/did), the
*stable, immutable* identifier for the account; it never changes for the life of
that account. In ATProto, the DID is the identity and the handle is UX.

Now suppose the registry keyed tenants on **handle**. Alice claims
`alice.example` → `alice.mosaic.example`. Later Alice lets `alice.example`
lapse; Mallory registers the domain and points its handle at *Mallory's* DID.
`identity.resolve("alice.example")` now resolves to Mallory's DID and Mallory's
PDS — and `alice.mosaic.example` would start serving **Mallory's** content under
Alice's address. A handle-keyed host is a handle-takeover-shaped hole.

Keying on **DID** closes the ownership half of that hole. The `Tenant` row that
grants `alice` the subdomain is bound to `did:plc:alice`, and it was created
only because someone completed an OAuth sign-in *as* `did:plc:alice`
([ATProto OAuth](https://atproto.com/specs/oauth)). Ownership is anchored to the
thing that can't be reassigned. This is the same discipline PR 5 applied to
caching — its identity and preview caches are **DID-scoped**, so a handle change
can never serve one actor's cached content under another's key — and it's the
same reason PR 1's exercise flagged "a user changes their handle" as the bug to
watch. Key state on the DID; treat the handle as a lookup input, never as the
primary key.

There is still a live seam here, and it's honest to name it: `tenant_home` calls
`identity.resolve(tenant.handle)` — it resolves the **stored handle** on each
render. If that handle has since been taken over, the *content* rendered could be
the new owner's, even though the *row* is still bound to Alice's DID. The
registry key is safe; the render input is not yet re-verified against it. This is
exactly the gap **PR 12's handle-takeover hardening** closes, by re-resolving and
checking that the handle still maps to the tenant's stored DID before rendering
(and by storing/refreshing the DID→handle binding rather than trusting a handle
captured at claim time). Keying on DID is necessary but not sufficient; PR 12 is
where the render path is made to *verify* the binding, not just store it.

## Design decisions & "why not X"

- **Why no content columns on `Tenant`?** Because the PDS is the source of truth.
  A registry that stored posts would be a second copy to keep in sync and a
  lock-in surface. "Row + handle reproduces the site" is only true if the row
  holds no content — so it holds none.
- **Why 404 unknown/suspended subdomains instead of falling through to the blog?**
  A host under the base domain that names no live tenant is unambiguously a
  mistake or a suspended site; serving the operator's blog there would be
  confusing and would leak operator content onto tenant-shaped URLs. Dead end,
  by design.
- **Why OAuth as the entire auth system — no local accounts?** Because the
  identity already exists in ATProto and is provable. Adding an email/password
  account would create a *weaker*, separately-attackable credential for
  something the DID already secures. The grant is the account.
- **Why built-in reserved names that config can't remove?** `oauth`, `admin`,
  `www` and friends are routing/impersonation hazards independent of any
  operator's preferences; making them un-removable is a safety floor, not a
  policy the operator should be able to lower.
- **Why a lazy OAuth import in the view?** So `import django_mosaic.hosted`
  succeeds without the `oauth` extra. The middleware, models, and admin have no
  crypto dependency; only `/claim` does, so only `/claim` pays for it.

## Exercises

1. **Trace a pass-through.** Follow a request to `mosaic.example/claim` through
   `TenantMiddleware.__call__` and `_subdomain`. Which branch returns `None`,
   and confirm `request.urlconf` is never set so `ROOT_URLCONF` (with `/claim`)
   is used. Now do the same for `notmosaic.example` — which check saves you?
2. **Spot the takeover.** Write down the concrete sequence by which keying on
   *handle* would let Mallory serve content at Alice's subdomain. Then identify
   the one line in `tenant_home` that still trusts a possibly-stale handle, and
   sketch the check PR 12 adds. Which is the *key*, and which is the *input*?
3. **Predict the behavior.** A visitor signed in as `did:plc:alice` (already
   owning `alice`) POSTs `/claim` with `subdomain=second`. What status and what
   DB state result, and which line enforces one-subdomain-per-DID? Verify against
   `test_existing_tenant_shown_not_duplicated`.
4. **Reserved-name reasoning.** Add `"founder"` to `RESERVED_SUBDOMAINS` and
   confirm via `conf.reserved_subdomains()` it merges with the built-ins. Then
   explain why `mosaic` and `oauth` are in `BUILTIN_RESERVED` but `founder`
   isn't — what class of harm is the built-in list guarding against?
5. **Hands-on middleware.** In a scratch project, add `TenantMiddleware`, set
   `BASE_DOMAIN` and `ALLOWED_HOSTS=[".mosaic.example"]`, create a `Tenant` in
   the shell, and hit the subdomain with
   `curl -H "Host: you.mosaic.example" localhost:8000/`. Then suspend the tenant
   in the admin and re-curl — observe the 404 on the very next request.

## Verify it yourself

```bash
git checkout learn/07-tenancy
python -m pytest tests/test_hosted.py -q          # skips cleanly without the oauth extra
git show 3075a74 -- src/django_mosaic/hosted/middleware.py   # the Host-header router
git show 3075a74 -- src/django_mosaic/hosted/views.py        # tenant_home + claim
```

## Glossary

- **Tenant** — one hosted personal site: a `Tenant` row binding a DID to a
  subdomain and a status.
- **DID** — the immutable account identifier the registry is keyed on
  (`did:plc:…`). Never reassigned.
- **Handle** — the reassignable, human-friendly pointer to a DID
  (`alice.example`). Stored for display, never used as the key.
- **Base domain** — the apex (`mosaic.example`) tenants get subdomains of; its
  own Host passes through to the operator's URLconf.
- **`request.urlconf`** — the per-request attribute that, when set by middleware,
  replaces `ROOT_URLCONF` for that request. The multi-tenancy seam.
- **Tenant URLconf** — `django_mosaic.hosted.tenant_urls`, the one-route URL map
  swapped in for tenant hosts.
- **Claim** — the OAuth-gated act of binding your signed-in DID to a subdomain.
- **Reserved subdomain** — a name that can never be claimed; built-in
  infrastructure names plus the operator's `RESERVED_SUBDOMAINS`.
- **Suspended** — a tenant status that stops the host serving on the next
  request, set by the admin `suspend` action.
