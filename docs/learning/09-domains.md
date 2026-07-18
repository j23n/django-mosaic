# PR 9 — Custom domains, domain-as-handle, and abuse reports

> **Stack:** 9/12 · **base:** `learn/08-dashboard`
> **Commit:** `42d5524` · **What it adds:** tenants can bind their **own
> domain** to a hosted site, verified *operationally* (the cert we issue is
> the proof, not a TXT challenge); that domain can then become the tenant's
> ATProto **handle**; and an anonymous **/report** form feeds an admin
> moderation queue with resolve / suspend actions.

## The one-sentence version

A tenant types `blog.example.com` into the dashboard; they point DNS at us; the
**first HTTPS request that actually arrives** proves the domain resolves to a
cert we chose to issue (via an on-demand-TLS `ask` endpoint that only says
"yes" for registered domains) — so verification is a side effect of being
served, and once connected the same host answers `/.well-known/atproto-did`,
letting the domain become the tenant's handle.

## Learning objectives

**ATProto**

- **Domain-as-handle**: how serving `/.well-known/atproto-did` lets a domain you
  control become your handle, and how it ties back to PR 1's identical endpoint.
- The handle spec's **two verification methods** (DNS `TXT` vs HTTPS
  well-known) and why mosaic implements the HTTPS one for tenants.
- Why handle resolution is **bidirectional**: the domain must resolve to the
  DID *and* the DID document must claim the domain back (`alsoKnownAs`) — one
  direction alone is a spoof.

**Python / Django + ops**

- **On-demand TLS** and the **`ask` endpoint** pattern: your proxy asks your app
  "should I get a certificate for this Host?" before touching ACME.
- **Operational / implicit verification** vs a DNS-`TXT` challenge — the
  trade-offs, and the **ingress-trust assumption** it rests on.
- **Anti-squatting** via a staleness/reclaim gate: registering a string costs
  nothing, so an unverified registration must stay reclaimable.
- **Abuse-report anti-spam**: a **honeypot** field plus a **per-IP throttle**
  built on Django's cache (`cache.add` + `cache.incr`) that **fails silently**.
- **Admin moderation actions**: bulk `resolve` and `suspend_tenant`, and how
  suspension propagates to routing *and* cert issuance.

## Grounding: official docs

Read these first; the code is a thin layer over them.

- Handle spec — DNS + well-known verification, bidirectional resolution —
  <https://atproto.com/specs/handle>
- DID (and the DID document's `alsoKnownAs`) — <https://atproto.com/specs/did>
- Caddy automatic HTTPS — **on-demand TLS** and the `ask` endpoint —
  <https://caddyserver.com/docs/automatic-https#on-demand-tls>
- Overview & mental model — <https://atproto.com/guides/overview>
- Django cache framework (the throttle's `cache.add`/`incr`) —
  <https://docs.djangoproject.com/en/stable/topics/cache/>
- PR 1's well-known handle endpoint (the single-tenant version this PR
  multi-tenantizes) — `docs/learning/01-atproto-bridge.md`, §6.

## Background: the model this PR implements

By PR 7 a request's **Host header** already routes to a `Tenant`: middleware
maps `alice.mosaic.example` → the tenant whose `subdomain="alice"`, sets
`request.tenant`, and swaps in the tenant URLconf. PR 9 adds a *second* way for
a host to name a tenant — an **arbitrary domain the tenant owns** — and it has
to solve a problem subdomains never had: **proving the tenant controls the
domain** before we serve it or issue TLS for it.

The clever move is that mosaic never runs a verification *ceremony*. There is no
"paste this TXT record and click Verify" button. Instead:

```
register domain string  ──►  (nothing served yet, no cert)
        │
tenant points DNS at us
        │
first HTTPS request  ──►  proxy asks "cert for blog.example.com?"
        │                  app: only if a tenant registered it  ──► ACME issues
        ▼
request reaches Django with Host: blog.example.com
        │                  the request itself is the proof of control
        ▼
middleware stamps domain_verified_at = now()
```

Two independent gates make issuance safe: DNS has to point at us (or ACME can't
validate), **and** we have to have said "yes" at the `ask` endpoint (or the
proxy never asks ACME at all). A domain registered but never pointed at us just
sits there, unverified and reclaimable — that's the anti-squatting story.

## Guided tour of the diff (read in this order)

### 1. `hosted/conf.py` — one new knob

`DOMAIN_TARGET` joins the `MOSAIC_HOSTED` dict, with a `domain_target()` helper
that falls back to `base_domain()`. This is *only* the string shown in the DNS
instructions ("point your CNAME at …"); the app never reads it back. Same
inert-until-configured discipline as PR 1's `atproto/conf.py` — the whole public
surface is one settings dict.

### 2. `hosted/models.py` + migration `0002` — the new state

Two additions to `Tenant` and one new model:

```python
domain_validator = RegexValidator(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    "Enter a bare domain name like blog.example.com …",
)
```

- `custom_domain` — `CharField(max_length=253, null=True, unique=True,
  validators=[domain_validator])`. Note the shape: a **bare hostname**, dotted
  lowercase labels, alphabetic TLD, no scheme, no path. `unique=True` is doing
  real work — it's the DB-level guarantee that two tenants can't claim the same
  host (belt-and-suspenders with the view's pre-check).
- `domain_verified_at` — `DateTimeField(null=True)`. **Null means "registered
  but not yet proven."** This single nullable timestamp is the entire
  verification state machine.
- `Report` — `tenant` FK, `reason` (`TextField(max_length=2000)`),
  `reporter_contact` (optional), `created_at`, `resolved_at` (null = open).

The migration is a routine `AddField`/`AddField`/`CreateModel`. Nothing exotic
here — contrast with PR 4's state-only migration; this one really does change
the schema.

> **Why `null=True` *and* `blank=True` on a `CharField`?** Normally Django
> convention is `blank=True` only (empty string, not NULL) for strings. But
> `unique=True` + many empty strings would collide — every unclaimed tenant
> would have `custom_domain=""` and violate uniqueness. `NULL`s are exempt from
> a unique constraint, so "no domain" *must* be `NULL` here. This is the one
> idiomatic exception to "never null a CharField."

### 3. `hosted/middleware.py` — routing + implicit verification

The old `__call__` only knew subdomains. Now it tries a custom domain **first**,
then falls back to the subdomain path:

```python
tenant = self._custom_domain_tenant(request)
if tenant is None:
    subdomain = self._subdomain(request)
    ...
if tenant is not None:
    if tenant.status != Tenant.STATUS_ACTIVE:
        raise Http404("This site is unavailable.")
    request.tenant = tenant
    request.urlconf = "django_mosaic.hosted.tenant_urls"
```

`_custom_domain_tenant` is the heart of the PR:

```python
host = request.get_host().split(":", 1)[0].lower().strip(".")
base = conf.base_domain()
if host == base or host.endswith("." + base):
    return None                      # our own domain — not a custom one
tenant = Tenant.objects.filter(custom_domain=host).first()
if tenant is not None and tenant.domain_verified_at is None:
    tenant.domain_verified_at = timezone.now()
    tenant.save(update_fields=["domain_verified_at"])
```

Read the ordering carefully:

1. **Skip our own hosts.** Anything at or under `base_domain` is handled by the
   subdomain path; only *foreign* hosts are candidate custom domains.
2. **Exact match** on `custom_domain`. No wildcards, no suffix matching — a
   custom domain names exactly one tenant.
3. **First hit stamps `domain_verified_at`.** The comment says it plainly: *a
   request arriving here proves the domain resolves to us over a cert we issued,
   so the first hit counts as verification.* The suspension check happens back
   in `__call__` **after** this returns, so a suspended tenant still 404s — but
   note the stamp already happened. (Harmless: verification just records
   reachability; serving is gated separately on status.)

> **Review question.** Why is it safe to *write to the database on a GET*, in
> middleware, on every unverified custom-domain request? Two reasons: it's
> idempotent (guarded by `domain_verified_at is None`, so exactly one write ever
> happens per domain), and `update_fields=["domain_verified_at"]` touches a
> single column. The *cost* is one extra `UPDATE` on the very first request to a
> new domain — negligible. What it is **not** safe against is a forged Host
> header from an untrusted ingress; see the deep dive.

### 4. `hosted/views.py` — four new endpoints

Read them in this order.

**`domain_check` — the `ask` endpoint.** The smallest, most load-bearing view:

```python
domain = (request.GET.get("domain") or "").lower().strip(".")
if domain and Tenant.objects.filter(
        custom_domain=domain, status=Tenant.STATUS_ACTIVE).exists():
    return HttpResponse("ok", content_type="text/plain")
return HttpResponse("unknown domain", content_type="text/plain", status=404)
```

Caddy (or any proxy) calls this **before** attempting ACME for an unknown SNI.
`200` ⇒ "go ahead, get a cert"; anything else ⇒ "don't." The filter is
`status=STATUS_ACTIVE`, so **suspending a tenant also stops new cert issuance**
for their domain — one status field gates routing *and* TLS.

**`tenant_wellknown_did` — domain-as-handle.** Served under the *tenant*
URLconf, so `request.tenant` is already set:

```python
tenant = getattr(request, "tenant", None)
if tenant is None:
    raise Http404("Not a tenant host.")
return HttpResponse(tenant.did, content_type="text/plain")
```

This is PR 1's `wellknown_atproto_did` made multi-tenant. In PR 1 the DID came
from `conf.get_setting("DID")` (one site, one DID); here it comes from
`request.tenant.did` (the DID the routing already resolved). Same three-line
body, same content type — the difference is *where the DID comes from*.

**`dashboard_domain` — register / remove.** POST-only. Finds the signed-in
tenant via the OAuth session (PR 6), then either clears the domain (`remove`) or
validates and sets a new one through `_domain_error`:

```python
def _domain_error(domain, tenant):
    if not domain: return "Enter a domain."
    try: domain_validator(domain)
    except ValidationError: return domain_validator.message
    base = conf.base_domain()
    if domain == base or domain.endswith("." + base):
        return f"Domains under {base} are assigned via your subdomain."
    if Tenant.objects.exclude(pk=tenant.pk).filter(custom_domain=domain).exists():
        return "That domain is already registered to another site."
    return None
```

Setting a domain always resets `domain_verified_at = None` — a re-registered or
changed domain must re-prove itself. The error is stashed in the session and
popped by the dashboard view (PRG — post/redirect/get — so a refresh doesn't
resubmit).

**`report` — the abuse form.** `GET` renders the form; `POST` files a report.
Walk the guard order, because it's deliberate:

```python
if request.POST.get("website"):            # 1. honeypot → fake success
    return render(..., {"submitted": True})
if tenant is None:                          # 2. unknown site → 400
    ...
reason = (request.POST.get("reason") or "").strip()[:2000]
if not reason:                              # 3. empty reason → 400
    ...
ip = request.META.get("REMOTE_ADDR", "")
throttle_key = f"mosaic_hosted:report_rate:{ip}"
if not cache.add(throttle_key, 1, timeout=3600):   # 4. per-IP throttle
    try: count = cache.incr(throttle_key)
    except ValueError: count = 1
    if count > REPORTS_PER_HOUR:
        return render(..., {"submitted": True})     # fake success again
Report.objects.create(tenant=tenant, reason=reason, ...)
```

The throttle idiom is worth memorising: `cache.add(key, 1, timeout=3600)`
returns `True` only if the key was **absent** (first request in the window,
sets a 1-hour TTL); on subsequent hits it returns `False`, and `cache.incr`
bumps the counter atomically. Past `REPORTS_PER_HOUR` the view **pretends to
succeed** — same "Thanks, received" page an honest reporter sees. That's
intentional: an abuser who gets a visible "rate limited" error learns exactly
where the wall is and tunes against it; a silent wall gives no signal. Same
reasoning behind the honeypot's fake-success. (The `except ValueError` guards
the race where the key expires between `add` and `incr`.)

### 5. `hosted/admin.py` — the moderation queue

`ReportAdmin` lists reports, filters open-vs-resolved with an
`EmptyFieldListFilter` on `resolved_at`, and adds two bulk actions:

```python
@admin.action(description="Suspend the reported tenants")
def suspend_tenant(self, request, queryset):
    Tenant.objects.filter(reports__in=queryset).update(
        status=Tenant.STATUS_SUSPENDED)
    queryset.update(resolved_at=timezone.now())
```

One click on a batch of reports suspends every tenant they point at *and* marks
those reports resolved. Because suspension is just `status`, its effect is
immediate and total: the middleware 404s the site on the next request, and
`domain_check` stops answering `ok`, so the proxy won't renew or issue certs.
`TenantAdmin` also grows `custom_domain` / `domain_verified_at` columns (the
latter read-only — it's stamped by the system, never edited).

### 6. `tenant_urls.py`, `urls.py`, templates

- `tenant_urls.py` adds `path(".well-known/atproto-did", tenant_wellknown_did)`
  — served on **every** tenant host (subdomain and custom domain alike; the test
  checks both).
- `urls.py` (base domain) adds `dashboard/domain`, `domains/check`, and
  `report`.
- `dashboard.html` gains a **Custom domain** section: connect form when none is
  set; when set-but-unverified, the CNAME-target instructions; when verified, a
  `<details>` **"Use this domain as your ATProto handle"** wizard that points the
  user at Bluesky → Settings → Handle → "I have my own domain" → **"No DNS
  panel"**. `home.html` footer gets a `· report` link (hand-reversed against the
  base domain, since the tenant URLconf has no `hosted-report` route).
- `report.html` carries the honeypot (`.hp { position:absolute; left:-9999px }`)
  and `<meta name="robots" content="noindex">`.

### 7. `tests/test_hosted_domains.py`

Skim for the discipline. Highlights: `test_custom_domain_routes_and_verifies`
asserts the first request stamps and the second does **not** re-stamp;
`DomainCheckTest` proves `?domain=Alice.Blog.` normalises and that suspended
tenants are refused; `test_throttled_after_limit` posts `REPORTS_PER_HOUR + 3`
reports and asserts exactly `REPORTS_PER_HOUR` rows persisted; the honeypot test
asserts a `200` with **zero** rows. These tests pin the two anti-abuse
behaviours (silent throttle, fake-success honeypot) so a future refactor can't
quietly turn them into loud errors.

## Deep dive: on-demand TLS, the `ask` endpoint, and operational verification

The traditional way to let customers bring a domain is a **challenge**: you
generate a token, tell them to publish it as a DNS `TXT` record or at a
well-known URL, then poll until you see it, and only *then* provision. It works,
but it's a whole stateful ceremony — pending tokens, expiry, a "Verify" button,
retries.

mosaic throws the ceremony out and leans on a fact that's already true: **you
cannot serve HTTPS for a domain you don't control**, because you can't get a
valid certificate for it. So instead of verifying *then* serving, mosaic
**serves, and treats successful service as the verification.**

The mechanism is Caddy's **on-demand TLS**. Normally a TLS server needs every
certificate up front. On-demand TLS flips that: when a TLS handshake arrives for
a hostname Caddy has no cert for, Caddy obtains one **during the handshake**.
Left unbounded that's a footgun — anyone who points any domain at your IP makes
you fire off ACME orders, and you'll blow through Let's Encrypt rate limits or
issue certs for hosts you have no intention of serving. So Caddy strongly
recommends an **`ask` endpoint**: before it attempts issuance for an unknown
name, it makes an HTTP `GET` to your URL with `?domain=<the SNI name>`, and only
proceeds if you answer **`2xx`**. Any other status ⇒ no cert.

`domain_check` *is* that endpoint. It answers `200` for exactly the set of
domains that (a) some tenant registered and (b) belong to an **active** tenant.
That gives issuance two independent gates:

1. **The `ask` gate** (application policy): "is this a domain we agreed to
   serve?" — cheap, instant, our own DB.
2. **The ACME gate** (control proof): "can this server actually complete the
   ACME challenge for the domain?" — which only passes if DNS genuinely points
   at us. *This* is the real ownership proof; the `ask` endpoint alone proves
   nothing (a tenant could register any string).

Only when both pass does a cert exist, and only then can a request with that
Host reach Django — at which point the middleware stamps `domain_verified_at`.
So verification is **implicit and operational**: it is a *consequence* of the
first served request, not a separate step.

**Trade-offs vs a DNS-`TXT` challenge:**

| | Operational (this PR) | DNS-`TXT` challenge |
|---|---|---|
| State to manage | one nullable timestamp | pending tokens, expiry, polling |
| UX | point DNS, load the page | copy token, paste, click Verify, wait |
| Proves | reachability + cert issuance | DNS record control |
| Works for apex (no CNAME)? | needs ALIAS/ANAME or A record | yes |
| Depends on | trusted ingress terminating TLS | nothing external |

**The ingress-trust assumption (read this twice).** Operational verification is
only sound because of *who sets the `Host` header*. The chain "a request with
`Host: blog.example.com` arrived ⇒ the domain is ours" holds **only if that
request necessarily came through TLS termination for a cert we issued.** In the
intended topology — Caddy terminates TLS on-demand and reverse-proxies to
Django — that's guaranteed: to send us `Host: blog.example.com`, an attacker
would have had to complete a TLS handshake Caddy only performs for a cert it
issued for a domain that passed the `ask` gate *and* ACME.

But `Host` is just a header. If Django is ever reachable **directly** (not only
through the trusted proxy), anyone can `curl -H 'Host: victim.blog'
http://your-django:8000/` and — because the middleware trusts the Host — stamp
`domain_verified_at` on a domain they don't own, or (worse, combined with
registration) hijack routing. The safety rests entirely on the **ingress being
the only path to the app**. Two consequences for a real deployment:

- Bind the app to loopback / a private network; let *only* the proxy reach it.
- Keep `ALLOWED_HOSTS` honest in production (the tests use `["*"]`, which you
  must **not** copy to prod — that's a test convenience, not a template).

This is the same class of assumption every "trust `X-Forwarded-For` / `Host`
from the proxy" setup makes; the lesson is to *name* it, because operational
verification silently depends on it. (PR 12's adversarial review revisits
header-trust; keep this in your back pocket.)

**Anti-squatting / the reclaim gate.** Registering a `custom_domain` string is
free and instant, which invites squatting ("register everyone's domain so they
can't"). The defence is that a registration is *inert until verified*: an
unverified row (`domain_verified_at is None`) never got a cert, never served a
request, and — per the commit — is **clearable in the admin**. Because
uniqueness is only meaningful once a domain is actually *connected*, a stale
unverified claim can be reclaimed: an operator (or a future automated
staleness sweep) can null out a `custom_domain` that never verified within some
window, freeing it for its real owner. The single `domain_verified_at`
timestamp is what makes "connected" and "merely claimed" distinguishable — and
therefore what makes reclaim safe.

## Deep dive: domain-as-handle (and why it's bidirectional)

An ATProto **handle** is a domain name that resolves to your **DID**. The handle
spec defines **two** verification methods, and a handle is valid if *either*
succeeds:

1. **DNS `TXT`** — a record at `_atproto.<handle>` whose value is
   `did=did:plc:…`.
2. **HTTPS well-known** — `GET https://<handle>/.well-known/atproto-did` returns
   the DID as `text/plain`.

mosaic serves method 2. Once `blog.example.com` is a connected custom domain,
`https://blog.example.com/.well-known/atproto-did` returns the tenant's DID — so
the tenant can go into Bluesky, Settings → Handle → "I have my own domain" →
**"No DNS panel"**, and Bluesky will fetch that URL, see the DID, and let them
adopt `blog.example.com` as their handle. The domain they're *already hosting
their site at* becomes their *identity*. That's the "your domain is your
identity" move — and it costs mosaic exactly the three-line view from §4,
because the plumbing (routing → `request.tenant.did`) already exists.

But serving the DID is only **half** the proof, and this is the crucial spec
detail. Resolution is **bidirectional**:

- **Forward** (handle → DID): the well-known endpoint (or `TXT`) says
  "`blog.example.com` claims `did:plc:alice`."
- **Backward** (DID → handle): the DID **document** must list the handle in its
  **`alsoKnownAs`** array as `at://blog.example.com`.

A resolver accepts the handle **only if both agree**. Why both? Because the
forward direction alone is forgeable in the trivial direction: *I* can stand up
`evil.example` and serve *your* DID at its well-known endpoint. If forward were
enough, I'd have "claimed" your DID as my handle. The backward check defeats
that — I can't add `at://evil.example` to *your* DID document, because updating
your DID doc requires *your* signing key. So the DID doc's `alsoKnownAs` is the
authoritative claim, and the well-known endpoint is the domain *confirming* a
claim the DID already made. Both must point at each other.

This is exactly the bidirectionality PR 1 flagged for the single-tenant case
("the domain claims the DID, the DID's doc claims the handle"). PR 9 changes
*nothing* about the mechanism — it just makes the "domain" side a per-tenant
value instead of one global setting. The tenant still has to do the backward
half themselves (Bluesky writes `alsoKnownAs` into their DID doc when they
confirm the handle change); mosaic only owns the forward half.

> **Review question.** A tenant connects `blog.example.com`, sees "✓ connected",
> opens the wizard, but the handle change in Bluesky fails. What's the most
> likely cause, given mosaic is serving the DID correctly? (Answer: the backward
> direction. Bluesky fetched `/.well-known/atproto-did` fine, but the tenant
> hasn't completed the flow that writes `at://blog.example.com` into their DID
> document's `alsoKnownAs`, or a cached DID doc hasn't refreshed. The forward
> proof mosaic controls is working; the bidirectional pair isn't closed yet.)

## Design decisions & "why not X"

- **Why implicit verification, not a `TXT`-challenge ceremony?** Because we're
  going to issue TLS anyway, and a successfully-served HTTPS request already
  proves more than a `TXT` record does (reachability *and* cert issuance). One
  nullable timestamp replaces a whole pending-token state machine. The cost is
  the ingress-trust assumption, which is acceptable for a proxy-fronted app.
- **Why gate the `ask` endpoint on `status=ACTIVE` instead of just existence?**
  So that suspension is a single lever. A suspended tenant's domain drops out of
  cert issuance *and* routing at once; you never have to separately "revoke the
  cert" — the proxy simply stops being told to keep it.
- **Why does the throttle fail *silently* (fake success)?** An error page is a
  free oracle: it tells an abuser the exact limit and lets them binary-search
  the throttle. Returning the honest "received" page denies them any feedback
  loop. Same reason the honeypot returns success rather than "spam detected."
- **Why `unique=True` at the DB level *and* a check in `_domain_error`?** The
  view check gives a friendly error in the normal case; the DB constraint is the
  actual guarantee under concurrency (two tenants racing to claim the same
  domain — the second `save()` raises `IntegrityError` rather than silently
  double-registering).
- **Why serve `/.well-known/atproto-did` on subdomain hosts too, not just custom
  domains?** It's harmless and uniform: `alice.mosaic.example` returning
  `did:plc:alice` means a tenant could even use their *subdomain* as a handle.
  One route, every tenant host, no special-casing.
- **Why reset `domain_verified_at = None` every time the domain is set?** A
  changed or re-added domain is a *different* control claim; the previous
  verification says nothing about the new DNS. Force a fresh first-request proof.

## Exercises

1. **Spoof the Host.** With the app bound to `0.0.0.0` and `ALLOWED_HOSTS=["*"]`,
   register `victim.blog` to a tenant, then `curl -H 'Host: victim.blog'
   http://127.0.0.1:8000/` directly (bypassing any proxy). Watch
   `domain_verified_at` get stamped. Now articulate, in one sentence, the
   deployment invariant that makes this *not* a vulnerability in production.
2. **Close the bidirectional loop by hand.** For a real DID you control, fetch
   its DID document (`https://plc.directory/<did>`), find `alsoKnownAs`, and
   confirm it lists your handle. Then fetch `https://<handle>/.well-known/
   atproto-did`. Show that flipping either side (wrong DID served, or handle
   missing from `alsoKnownAs`) breaks resolution.
3. **Predict the throttle boundary.** `cache.add` sets the key with a 1-hour TTL
   on the *first* request. If a reporter files 5 reports at 12:59 and 5 more at
   13:01, how many persist, and why isn't it a clean "5 per clock hour"? (Hint:
   the window is anchored to the first request, not the wall clock.)
4. **Reclaim a squat.** Register `taken.blog` to tenant A but never point DNS at
   it (`domain_verified_at` stays null). Write the admin/management step that
   safely reclaims it for tenant B. What condition must you check before nulling
   A's `custom_domain`, and why is `domain_verified_at IS NULL` the right gate?
5. **Read the handle spec.** Open <https://atproto.com/specs/handle> and
   confirm: does a resolver require *both* verification methods to agree, or is
   either sufficient for the forward direction? Where does the backward
   (`alsoKnownAs`) check live in the resolution algorithm?

## Verify it yourself

```bash
git checkout learn/09-domains
python -m pytest tests/test_hosted_domains.py -q          # 15 tests, no network
git show 42d5524 -- src/django_mosaic/hosted/middleware.py # implicit verify
git show 42d5524 -- src/django_mosaic/hosted/views.py      # ask + report + wizard
```

Then, against a scratch deployment behind Caddy: register a domain in the
dashboard, point a `CNAME` at `DOMAIN_TARGET`, and watch the dashboard flip from
"waiting for DNS…" to "✓ connected" on the first load — with no button pressed.

## Glossary

- **On-demand TLS** — obtaining a certificate during the TLS handshake for a
  hostname the server had no cert for, instead of requiring all certs up front.
- **`ask` endpoint** — an HTTP URL the proxy queries (`?domain=<host>`) before
  on-demand issuance; a `2xx` permits it, anything else denies it.
- **Operational / implicit verification** — treating a successfully-served
  request as proof of domain control, rather than running a separate challenge.
- **Ingress-trust assumption** — the requirement that the app is reachable *only*
  through the trusted proxy, so an incoming `Host` header can be believed.
- **Domain-as-handle** — using a domain you control as your ATProto handle by
  serving your DID at `/.well-known/atproto-did`.
- **Bidirectional resolution** — a handle is valid only if the domain resolves to
  the DID *and* the DID document's `alsoKnownAs` claims the handle back.
- **Honeypot** — a hidden form field real users leave empty and bots fill; a
  filled value marks the submission as spam.
- **Silent throttle** — a rate limit that returns a normal-looking success once
  exceeded, denying an abuser any signal to tune against.
- **Staleness / reclaim gate** — the rule that an unverified (never-connected)
  registration can be released, preventing squatting on domain strings.
</content>
</invoke>
