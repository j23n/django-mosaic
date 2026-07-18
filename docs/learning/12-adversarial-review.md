# PR 12 — The adversarial review round (the capstone)

> **Stack:** 12/12 · **base:** `learn/11-jetstream` · **Commits:**
> `6a3b2e2…8da3fa2` (inclusive, 5 commits) · **What it adds:** no new features — five review passes
> over the whole ATProto/hosted surface, turned into fixes. This is the lesson
> on *reading your own code as an attacker*.

## The one-sentence version

Every URL, token, record, and Host header that mosaic handles came, at some
point, from someone who is not you — a DID document, an authorization-server
metadata blob, a callback query string, a tenant's own repo, an ingress proxy —
and this PR walks the surface *assuming each of those is hostile*, then closes
the holes: SSRF in the PDS/auth-server fetch path, the OAuth mix-up attack, a
handle-takeover content hijack, a firehose subscription that silently widened to
the whole network, session fixation, and URL names that could shadow a
consumer's.

## Learning objectives

**ATProto**

- **SSRF is structural in ATProto**, not incidental: PDS and
  authorization-server URLs are *discovered* from DID documents and metadata you
  don't control, so they must be validated **before** you connect — and DNS-
  rebinding-aware (a public-looking name can resolve to `169.254.169.254`).
- The **OAuth mix-up attack** and why RFC 9207's `iss` authorization-response
  parameter must be *present and checked*, never defaulted to the expected
  issuer.
- Why keying tenancy on the **immutable DID** (not the reassignable handle)
  defeats a handle-takeover hijack — the concrete payoff of "identity is a DID"
  from the primer.
- The **blast radius of an empty filter**: a Jetstream `wantedDids` set that
  silently emptied subscribes you to the *entire* firehose.

**Python / Django**

- Server-side request forgery **defense in depth**: URL validation +
  `getaddrinfo` → reject private/reserved IPs + `allow_redirects=False` + treat
  any `3xx` as an error.
- **Session fixation** and `request.session.cycle_key()` before you store an
  identity in the session.
- **URL namespacing**: `app_name` / `include((patterns, "ns"))` /
  `reverse("mosaic:…")`, and when a mounted third-party app must stay *out* of
  your namespace.
- `select_for_update` to **serialize a single-use token rotation**; `sync_to_async`
  for ORM/cache I/O inside an async loop.
- **Adversarial review as a discipline** — a repeatable way to walk a surface
  and find these before an attacker does.

## Grounding: official docs

Each fix below traces to a named attack class. Read the source, not just the fix.

- SSRF — the attack: <https://owasp.org/www-community/attacks/Server_Side_Request_Forgery>;
  the prevention cheat sheet (why a blocklist of *names* is not enough):
  <https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html>
- OAuth mix-up / the `iss` response parameter — RFC 9207:
  <https://www.rfc-editor.org/rfc/rfc9207>; ATProto's OAuth profile:
  <https://atproto.com/specs/oauth>
- DID vs handle (why takeover matters) — <https://atproto.com/specs/did>,
  <https://atproto.com/specs/handle>
- Session fixation — <https://owasp.org/www-community/attacks/Session_fixation>;
  Django sessions & `cycle_key()`:
  <https://docs.djangoproject.com/en/stable/topics/http/sessions/>
- URL namespaces — <https://docs.djangoproject.com/en/stable/topics/http/urls/#url-namespaces>
- `select_for_update` — <https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update>
- Django async (`sync_to_async`) — <https://docs.djangoproject.com/en/stable/topics/async/>
- Jetstream — <https://github.com/bluesky-social/jetstream>

## Background: the trust boundary this PR redraws

Up to PR 11 the code *worked*. What this PR asks is a different question: for
each byte that crosses into mosaic, **who wrote it, and what happens if they were
malicious?** Map the inputs:

| Input | Written by | Trusted before? | Attack it enables |
|-------|-----------|-----------------|-------------------|
| PDS URL in a DID doc | whoever controls the DID | yes | SSRF to an internal service |
| Auth-server metadata endpoints | the auth server (arbitrary) | yes | SSRF with your client assertion attached |
| `iss` callback param | the redirecting browser | *defaulted* | OAuth mix-up (code redemption at the wrong AS) |
| Tenant handle → PDS | whoever holds the handle *now* | yes | handle-takeover content hijack |
| `wantedDids` result | the ORM (or a swallowed exception) | yes | subscribe to the whole firehose |
| Pre-auth session cookie | the visitor | reused | session fixation |
| Records in a tenant's repo | the tenant (any shape) | yes | 500 the public page / data loss |
| `Host` header | the ingress proxy | yes | domain verification spoof |

Every row below is one of those turned from *yes* into *validated*. The through-
line: **the network boundary is not where you think it is.** A DID document is
attacker-controlled input the moment you resolve an arbitrary handle — which is
exactly what "preview mode" (PR 5) and "hosted tenancy" (PR 7) made mosaic do.

## Guided tour (grouped by vulnerability class)

Read these as an attacker would: find the input, follow it to the dangerous
sink, then see the guard that was inserted between them.

### 1. SSRF — the discovered-URL fetch (`atproto/client.py`, `oauth/flow.py`)

**The attack.** mosaic resolves an arbitrary handle → DID → DID document, and
pulls a PDS `serviceEndpoint` out of that document. In preview/hosted mode the
DID belongs to *anyone*, so the "PDS URL" is attacker-chosen. Point it at
`http://169.254.169.254/latest/meta-data/` (the cloud metadata service) or
`http://localhost:5432` and mosaic will happily connect from inside your
network. The OAuth path is worse: the authorization server is discovered from an
attacker-served `oauth-protected-resource` document, and mosaic POSTs a
`private_key_jwt` assertion and DPoP proofs to *its* advertised endpoints — so a
naive client hands its credentials to the internal host it was tricked into
calling.

**The fix.** `client._validate_pds_url()` requires `https`, rejects IP-literal
hosts, and — the new part — resolves the hostname and refuses any name that maps
to a private/reserved address (`client._resolves_to_public_ip`). OAuth reuses
exactly this via `flow._safe_endpoint()`, applied to the discovered issuer
(`authorization_server_for`) **and** every endpoint in its metadata
(`authorization_server_metadata`) *before* any assertion is sent. Then the depth:
`client._http_get`/`_http_post` set `allow_redirects=False`, and
`_raise_for_error` treats a `300–399` as an error — because a validated public
endpoint could otherwise `302` the request (credentials attached) to an internal
host. See the full chain in the deep dive.

**The lesson.** Validating a URL string is table stakes; a name-based check can't
see where DNS actually points, and a redirect defeats a check performed only on
the first URL. SSRF defense is a *chain*, and it must wrap the fetch, not just
precede it.

### 2. OAuth mix-up — the mandatory `iss` (`oauth/flow.py::complete_auth`)

**The attack.** In the OAuth *mix-up* attack (RFC 9207), a client that talks to
more than one authorization server can be tricked into redeeming an
authorization code at the *wrong* AS, leaking the code (and thus account access)
to an attacker-controlled server. The defense is the `iss` authorization-
response parameter: the callback must name the issuer that produced it, and the
client must check it against the AS it started the flow with. The pre-PR code
checked it — but with `params.get("iss", pending["issuer"])`: **a missing `iss`
defaulted to the expected value and silently passed.** An attacker who can strip
the parameter bypasses the entire defense.

**The fix.** A missing `iss` is now a hard failure:

```python
callback_iss = params.get("iss")
if not callback_iss:
    raise OAuthError("Callback is missing the required `iss` parameter.")
if callback_iss.rstrip("/") != pending["issuer"]:
    raise OAuthError("Issuer mismatch in callback.")
```

**The lesson.** `dict.get(key, default)` is a footgun on a security check: it
converts "the attacker omitted the field" into "use the safe value." A required
security parameter must be *required* — absence is a failure, never a default.

### 3. Rotating-refresh-token burn (`oauth/flow.py::refresh`)

**The attack.** ATProto refresh tokens are single-use and rotating: spending one
returns a new one and invalidates the old. If two workers refresh the same
session concurrently, both read the same token, both spend it, one wins, and the
loser's now-invalid token is written back — the account is locked out until a
human re-authorizes. Not an external attacker, but a self-inflicted DoS that a
burst of traffic triggers reliably.

**The fix.** Serialize with a row lock and re-read under it:

```python
with transaction.atomic():
    locked = OAuthSession.objects.select_for_update().filter(pk=session.pk).first()
    if locked.refresh_token != stale_token:
        # Someone refreshed while we waited for the lock — adopt their tokens.
        session.refresh_token = locked.refresh_token
        ...
        return session
    tokens, _ = _post_with_dpop(locked.token_endpoint, {...}, locked.dpop_jwk)
```

The second worker blocks on `select_for_update()`, wakes to find the token
already rotated, and **adopts the winner's result** instead of burning its stale
one. (Related: `xrpc_call`'s retry loop was widened to 4 and its nonce-retry and
refresh-retry budgets separated, so a recoverable DPoP-nonce demand can't exhaust
the loop and get mis-reported as a hard failure.)

**The lesson.** "Read, mutate, write-back" on a single-use resource is a race
unless the read is under a lock **and rechecked after acquiring it** — the
check-again-after-locking is what turns a lock into correctness.

### 4. Handle takeover — tenancy keyed on the DID (`hosted/views.py`, `atproto/identity.py`)

**The attack.** A hosted tenant claimed `alice.mosaic.example` while their handle
was `alice.bsky.social`. Handles are *reassignable* pointers to a DID; the DID is
permanent. The old render path resolved `tenant.handle` on every request. So if
Alice let her handle lapse (or it was taken over) and it now points at
**Mallory's** DID, `resolve(tenant.handle)` returns Mallory's PDS — and Alice's
subdomain silently serves Mallory's content. Ownership was proven on the DID at
claim time, but the render trusted the mutable handle.

**The fix.** Resolve the immutable DID, never the handle. `identity.resolve_did`
resolves a DID straight to a PDS (via `client.resolve_pds`), cached per DID, with
the handle carried through for display only:

```python
def _tenant_identity(tenant):
    # Ownership was proven on the DID; the handle is mutable and untrusted here.
    return identity_mod.resolve_did(tenant.did, handle=tenant.handle)
```

Every tenant view (`tenant_home`, `tenant_document`, `dashboard`, …) now goes
through it. (Jetstream complements this: on an `identity` event it drops the
`handle → DID` cache so a handle change can't linger for the cache TTL.)

**The lesson.** "Identity is a DID; the handle is a reassignable label" is not
trivia — it's an authorization invariant. Anywhere you make a trust decision,
key it on the thing that *can't be reassigned to someone else*.

### 5. The firehose blast radius (`atproto/jetstream.py::consume`, `build_url`)

**The attack.** `build_url` built `wantedDids` from `wanted_dids()`, an ORM call.
But `consume()` is an async loop, and calling the ORM from async raises
`SynchronousOnlyOperation` — which was swallowed by the "hosted app absent"
`except`. Result: `wanted_dids()` returned empty, and `urlencode([])` produced a
subscribe URL with **no `wantedDids` at all** — which Jetstream interprets as
*subscribe to every DID on the network*. mosaic would quietly firehose the entire
network into a single worker.

**The fix.** Two independent guards. First, marshal the DB/cache calls through
`sync_to_async` so they actually run (and `handle_event` too — a `DatabaseCache`
touches the ORM on invalidation). Second, `build_url` **refuses an empty set**,
and `consume` refuses to connect without DIDs:

```python
if not dids:
    raise ValueError("refusing to build a Jetstream URL with no wantedDids")
```

Plus operational hardening: back off even on a *clean* close (a bouncing server
otherwise hot-loops reconnects) and reset the backoff only after a live message
proves the connection works.

**The lesson.** Two lessons. (a) An empty filter is rarely "match nothing" — for
subscriptions and firehoses it usually means "match *everything*." Encode the
safe interpretation explicitly. (b) A swallowed exception (`except Exception:
pass`) doesn't make a bug go away; it makes it silent and *worse*. The catch that
was meant to tolerate an optional app hid a total-failure mode.

### 6. Session fixation — rotate before you store (`oauth/flow.py::complete_auth`)

**The attack.** An attacker plants a known session cookie in a victim's browser
(a set-cookie on a shared subdomain, a link with a session id — see the OWASP
page). The victim signs in; if the server keeps the *same* session id and just
adds the authenticated identity to it, the attacker — who knows that id — is now
authenticated as the victim.

**The fix.** One line, in the right place:

```python
request.session.cycle_key()   # new session id, same data
request.session[DID_KEY] = session.did
```

`cycle_key()` issues a fresh session key and invalidates the old one *before*
the DID (the authenticated identity) is written. The pre-auth cookie the attacker
knows is now dead. Note the test had to swap a bare `dict` for a real
`SessionStore` to exercise this — a dict has no `cycle_key`.

**The lesson.** Any privilege transition — anonymous → authenticated — must
rotate the session identifier. It's the session analogue of "never reuse a
credential across a trust boundary." Covered fully in the deep dive.

### 7. URL namespacing — `mosaic:` (and why martor stays out) (`urls.py`)

**The attack** (of the "reusable-app footgun" kind). mosaic is installed *into*
other projects. Its routes were registered with bare global names — `home`,
`feed`, `post-detail`. If the consumer project also has a `home`, the two
collide, and `reverse("home")` resolves to whichever loaded last: mosaic silently
shadows the host project, or vice versa.

**The fix.** Put the blog routes under an application namespace, and reverse
through it everywhere:

```python
blog_patterns = [ path("", home, name="home"), ... ]
urlpatterns = [
    path("martor/", include("martor.urls")),       # stays un-namespaced
    path("", include((blog_patterns, "mosaic"))),   # → "mosaic:home", …
]
```

Templates and models switch to `{% url 'mosaic:post-detail' %}` /
`reverse("mosaic:home")`. The subtlety: **martor is deliberately left out** of
the namespace, because martor reverses its own editor routes internally by bare
name (`martor_markdownfy`) — sweep it into `mosaic:` and its live preview breaks.
A data migration also seeds the `public`/`private` namespaces so a fresh install
serves `/` instead of 404-ing. Covered fully in the deep dive.

**The lesson.** A reusable app owns a *slice* of a project it doesn't control.
Namespace your names so you never shadow the host — but know which of your
mounted dependencies reverse by bare name and must be exempted.

### 8. Trusting your own stored data (`hosted/composer.py`, `hosted/site_settings.py`, `views.py`)

A cluster with one root cause: **"we wrote it, so it's safe" is false** the moment
the store is the user's own repo or a case-insensitive DB.

- **Data loss on a transient error.** `ensure_publication` treated *any*
  `AtprotoError` on the "does a publication record exist?" check as "no record" —
  and then created one, clobbering an existing record (possibly written by
  another standard.site app) in the user's repo. Fixed to overwrite **only** on a
  definitive `RecordNotFound`/`400`, and to raise a friendly retry on a 5xx or
  network error. The document write switched from `putRecord` to `createRecord`
  so a TID collision fails loudly instead of silently overwriting.
- **Hostile record shapes 500 the page.** Settings records come from the tenant's
  repo and can be any JSON shape. `clean_theme`/`css_variables`/`effective_sections`
  now route every access through `_as_dict()` and `isinstance`-guard membership
  tests (a hostile token could be an *unhashable list*, which raises on `x in {…}`).
  A malicious record can no longer take down a tenant's public page.
- **Case-gate bypass.** The `private/` token gate matches the path
  case-sensitively, but `namespace__name=…` is case-*insensitive* under some
  collations (MySQL default). So `/PRIVATE/…` resolved the gated namespace while
  slipping past the gate. `views._resolve_namespace` (and the RSS feed) now
  require an **exact-case** match.
- **Non-TID rkeys** hitting `/posts/<rkey>` are rejected by `composer.is_valid_tid`
  before they can pollute a cache key or waste a PDS round-trip.

**The lesson.** Validate on the **read** path, not just the write path — stored
data is input too, and the store may not enforce what you assumed (shape, case,
uniqueness). "I validated it when I saved it" doesn't survive a second app, a
different collation, or a hand-edited repo.

### 9. Domain squatting vs. domain hijack (`hosted/views.py::_register_domain`)

**The attack (two-sided).** Custom domains have no verification *token* —
ownership is proven operationally (DNS points at us, a cert issues, the first
request stamps `domain_verified_at`). That creates two opposite risks: a squatter
registering a string they don't control could *permanently block* the real owner;
but making unverified claims freely reclaimable lets an attacker *hijack* a
victim's freshly-pointed domain in the window before its first request verifies
it.

**The fix.** A *verified* domain is locked to its tenant. An *unverified* one is
reclaimable — **but only after it goes stale** (`DOMAIN_RECLAIM_HOURS`, default
72), a window that comfortably exceeds DNS propagation + first request. Concurrent
claims take a row lock and return a friendly 409 instead of an `IntegrityError`
500; suspended tenants are locked out of the write paths so they can't reclaim
via the domain path. The `Host`-header trust is documented as resting entirely on
the ingress (bind to `127.0.0.1`, terminate TLS at the proxy).

**The lesson.** When ownership is proven *operationally* rather than
cryptographically, the security lives in the *timing* and the *ingress*, not the
code alone — and both the block-forever and the hijack-race directions have to be
closed at once. Write the trust assumptions down; a threat model the operator
can't see is one they can't uphold.

## Deep dive: SSRF-in-depth, the full chain

The single most important idea in this PR: an ATProto client that resolves
arbitrary identities is an SSRF engine unless every hop is guarded. Here is the
complete chain in `atproto/client.py`, in the order a request flows through it.

**Step 1 — validate the URL string.** `https` only, and reject IP-literal hosts
outright (an attacker can't even *try* `https://10.0.0.1`):

```python
parts = urlsplit(url)
if parts.scheme != "https":
    raise AtprotoError(...)
# ... reject if host parses as an ipaddress literal ...
```

**Step 2 — resolve the name and inspect every address.** A blocklist of *names*
is useless against `pds.attacker.com → 169.254.169.254` or a wildcard DNS
service like `*.nip.io`. So resolve and check the actual IPs:

```python
def _ip_is_safe(ip):
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)

def _resolves_to_public_ip(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True   # doesn't resolve → can't connect → not an SSRF risk (fail open)
    return all(_ip_is_safe(info[4][0]) for info in infos)
```

Two deliberate calls: **`all(...)`** — a multi-record name is safe only if *every*
address is public (one internal A record is enough to attack). And **fail open on
`gaierror`** — a name that doesn't resolve can't be connected to, and failing
closed would break real hosts a sandbox simply can't resolve. The docstring is
honest that this does **not** stop *active DNS rebinding* (resolve-public-then-
flip-to-internal between the check and the connect); defeating that needs pinning
the socket to the validated IP, which is deferred.

**Step 3 — the fetch itself must not follow redirects.** Steps 1–2 validate *the
URL you are about to request*. A `302` to `http://169.254.169.254/` sends the
follow-up request — with any `Authorization`/DPoP header attached —
*unvalidated*. So the helpers hard-disable it:

```python
def _http_get(url, **kwargs):
    kwargs.setdefault("allow_redirects", False)
    return requests.get(url, **kwargs)
```

**Step 4 — treat a 3xx as an error, not a success.** With redirects off, a `302`
would otherwise return a 3xx response object that downstream code might read as
"fine." XRPC and DID-document endpoints never legitimately redirect, so:

```python
def _raise_for_error(resp):
    if 300 <= resp.status_code < 400:
        raise AtprotoError(f"Refusing to follow redirect ({resp.status_code}) ...")
    if resp.status_code >= 400:
        ...
```

**Step 5 — validate the `did:web` *document fetch*, not just its contents.** The
earlier code validated the PDS endpoint *inside* the returned document, but the
document URL itself is built from an arbitrary DID (`did:web:169.254.169.254`, or
`did:web:10.0.0.5%3a8443` hiding a port behind percent-encoding). So decode and
validate before fetching:

```python
domain = unquote(did.removeprefix("did:web:"))
doc_url = f"https://{domain}/.well-known/did.json"
_validate_pds_url(doc_url)          # the fetch is itself an SSRF vector
doc = _http_get(doc_url, timeout=timeout)
```

**Step 6 — bound the time.** Reads on the public render path use `READ_TIMEOUT`
(5s), not the 15s publish `TIMEOUT`, so a PDS that *accepts but never answers*
can't tie up a worker across every section of a preview page. A slow-loris PDS is
an availability attack; a short read timeout is the guard.

The OAuth layer (`oauth/flow.py`) is the same chain, reused: `_safe_endpoint`
wraps `client._validate_pds_url`, applied to the issuer and every metadata
endpoint before a client assertion is sent; `_get_json`, `_post_with_dpop`, and
`xrpc_call` all pass `allow_redirects=False` and treat `>= 300` as failure. One
validator, one no-redirect policy, applied at **every** boundary — that
uniformity is what makes it auditable.

> **Why not a library?** Because the danger is protocol-specific: the URLs come
> from DID documents and OAuth metadata, and the credential is attached to the
> request. A generic HTTP client can't know that `serviceEndpoint` is untrusted.
> The validation has to live where the trust boundary is.

## Deep dive: session fixation & URL namespacing

Two Django-craft fixes that look unrelated but share a shape: **a boundary the
framework won't police for you.**

### Session fixation, precisely

Django's session framework does *not* rotate the session key on login by itself —
`login()` does (via `cycle_key`), but a hand-rolled auth flow like this OAuth
callback does not. So when `complete_auth` stored the DID into whatever session
the request arrived with, it inherited the pre-auth session id. If that id was
attacker-planted, the attacker's cookie is now an authenticated session.

The fix orders two operations correctly:

```python
request.session.cycle_key()      # 1. new id, old id invalidated, data preserved
request.session[DID_KEY] = session.did   # 2. now write the identity
```

`cycle_key()` generates a new key and deletes the old session record, **keeping
the session data** — so the flow's own `pending`/`state` values survive, but any
id an attacker knew is dead. Order matters: rotate *before* the identity write, so
there is never a moment where the authenticated DID lives under the old id. See
<https://owasp.org/www-community/attacks/Session_fixation> and Django's
[session docs](https://docs.djangoproject.com/en/stable/topics/http/sessions/).
The test detail is instructive: it had to construct a real `SessionStore` instead
of a `dict`, because fixation defenses only exist on the real store.

### URL namespacing, precisely

`reverse("home")` searches a *flat* global registry of URL names. A reusable app
that ships bare names is gambling that no host project — and no *other* installed
app — uses the same one. `django-mosaic` ships `home`, `feed`, `post-detail`,
`tag-detail`, `draft-detail` — some of the most common names imaginable.

The mechanics of the fix are worth memorizing, because they're the standard
Django pattern:

```python
# Provide an application namespace by wrapping (patterns, "app_name") in include():
path("", include((blog_patterns, "mosaic")))
# Now every name is addressed as "mosaic:<name>":
reverse("mosaic:home")
{% url 'mosaic:post-detail' namespace year slug %}
```

Three consequences the PR handles:

1. **Every internal reference must move together.** Models (`get_absolute_url`),
   templates (`base.html`, `post-detail.html`), and the admin's draft-preview URL
   all switch to the `mosaic:` prefix in the same commit — a half-namespaced app
   throws `NoReverseMatch` at runtime. This is a **breaking change** for
   consumers, and the CHANGELOG says so.
2. **Mounted third-party apps that reverse by bare name must stay out.** Martor's
   JS calls `reverse("martor_markdownfy")` internally; there is no
   `martor:markdownfy`. So martor is mounted at the top level, *outside* the
   `mosaic` include, and kept *before* it so `martor/` isn't captured as a
   namespace segment. Namespacing is not "wrap everything" — it's "wrap what you
   own."
3. **The test URLconf must `include()` the app, not splice its `urlpatterns`.**
   `app_name`/namespace only registers when the urls module is `include()`d, so
   `tests/urls.py` switched from `+ mosaic_urls` to
   `path("", include("django_mosaic.urls"))` — mirroring how a real consumer
   wires it up, which is the only way the namespace test is meaningful.

The regression test captures the whole contract in one place: `reverse("mosaic:home")`
works, `reverse("home")` raises `NoReverseMatch`, and `reverse("martor_markdownfy")`
still works bare. Reference:
<https://docs.djangoproject.com/en/stable/topics/http/urls/#url-namespaces>.

## Design decisions & "why not X"

- **Why fail *open* on DNS resolution failure?** Because a name that doesn't
  resolve can't be connected to — it's not an SSRF vector — and failing closed
  would block legitimate hosts a sandbox can't resolve. The risk being defended
  is "resolves to something internal," not "resolves at all."
- **Why not defend active DNS rebinding now?** It requires pinning the socket to
  the validated IP (resolve once, connect to *that address* with the original
  Host header), which `requests` doesn't do out of the box. It's a known, honestly
  documented gap — see the note in `_resolves_to_public_ip` — deferred rather than
  half-done.
- **Why `createRecord` over `putRecord` for documents?** `putRecord` is
  idempotent-overwrite; on a TID collision it silently replaces. `createRecord`
  fails loudly. When the failure mode is "overwrite someone's data," *loud* is the
  safe default.
- **Why a staleness window for domain reclaim instead of never/always?** "Never
  reclaimable" lets squatters block real owners forever; "always reclaimable" opens
  a hijack race. A time window longer than DNS-propagation-plus-first-request
  closes both — the real owner always verifies first.
- **Why document the `Host`-trust model instead of enforcing it in code?** Because
  Django *can't* see the TLS cert; the guarantee genuinely rests on the ingress.
  The honest fix is to state the assumption (`bind 127.0.0.1`, TLS at the proxy)
  so the operator can uphold it — a security property you can't enforce, you must
  at least name.

## Exercises

1. **Trace an SSRF end to end.** Take `did:web:localhost` through `resolve_pds`.
   Which of the six guard steps stops it, and with what message? Now take a
   `did:plc:` whose document returns a PDS at `https://safe.example` that `302`s
   to `http://169.254.169.254`. Which step catches *that* one?
2. **Spot the default footgun.** The `iss` bug was `params.get("iss",
   pending["issuer"])`. Grep the codebase for other `.get(key, <sensitive
   default>)` patterns on untrusted input. Is any of them a silent bypass?
3. **Break the namespace.** Change one template back to `{% url 'home' %}` and run
   the suite. What error, and at what point (import? request? reverse?) does it
   surface — and why is that better than a silent shadow?
4. **Reason about the race.** In `refresh()`, delete the `if locked.refresh_token
   != stale_token` recheck but keep `select_for_update`. Construct the interleaving
   where two workers still both burn a token. (Hint: the lock serializes, but the
   *read* happened before it.)
5. **Empty-filter hunt.** `build_url` refuses an empty `wantedDids`. Find another
   place in the codebase where an empty collection would mean "match everything"
   or "affect everyone" rather than "nothing," and decide whether it's guarded.

## Verify it yourself

```bash
git checkout learn/12-adversarial-review
python -m pytest tests/test_atproto_preview.py tests/test_atproto_oauth.py \
                 tests/test_atproto_jetstream.py tests/test_review_fixes.py -q
git show 67907ea -- src/django_mosaic/atproto/client.py   # the full SSRF chain
git show 6a3b2e2 -- src/django_mosaic/atproto/oauth/flow.py  # iss + refresh lock
git show 8da3fa2 -- src/django_mosaic/urls.py             # the mosaic: namespace
```

## The course, in one page — what you learned across all 12 PRs

You started with a plain Django blog and ended with a multi-tenant, ATProto-native
hosted service. The arc:

- **PRs 1–4 — the bridge and its craft.** Identity resolution (handle → DID →
  PDS), records/blobs/AT-URIs over XRPC, `transaction.on_commit` for side effects,
  and a state-only FK migration. You learned ATProto's *shape*.
- **PRs 5–6 — reading anyone, then writing as anyone.** De-singletonizing to read
  arbitrary repos (and the first SSRF guard), then the full OAuth client — PAR,
  PKCE, DPoP, `private_key_jwt`, row-locked refresh. The read/write asymmetry made
  concrete.
- **PRs 7–9 — from personal to hosted.** DID-as-identity multi-tenancy, Host-header
  routing, settings-in-the-user's-repo, custom domains with on-demand TLS. Tenancy
  keyed on the immutable identifier.
- **PRs 10–11 — the write path and the firehose.** A composer minting TID rkeys and
  sanitizing render; the Jetstream consumer with `asyncio`, `sync_to_async`, and
  backoff. Scale, both directions.
- **PR 12 — adversarial review.** Everything above, re-read as an attacker. The
  meta-skill: for each input, *ask who wrote it and what if they were hostile*,
  then close the gap — SSRF in depth, the mix-up `iss`, DID-scoping, empty-filter
  blast radius, session fixation, namespacing.

The single most transferable idea is the one this PR is named for: **the trust
boundary is not where the network is; it's wherever untrusted bytes reach a
dangerous sink.** A DID document, a stored record, a `Host` header, a callback
param — each is an input, and reviewing your own code means walking each of them
to its sink as if you wrote it to break in.

### Still deferred (honest gaps)

The point of adversarial review is also knowing what you *didn't* fix. The
outstanding items, documented rather than hidden:

- **Token encryption at rest.** OAuth access/refresh tokens are stored in the DB
  in plaintext; revocation is "delete the row." Encrypting them at rest (KMS/
  envelope encryption) is deferred.
- **Active DNS rebinding / IP pinning.** `_resolves_to_public_ip` checks at
  validation time but doesn't pin the socket to the validated address, so a name
  that flips between check and connect is not defended. Requires a custom
  connection adapter.
- **Martor endpoint gating.** The martor editor/upload routes are mounted at the
  top level and are not themselves permission-gated in this app; a consumer that
  exposes them must gate them (they assume an authenticated admin context).

A hardened surface is not a *finished* one — it's one whose remaining risks are
named, bounded, and written down. That's where the course ends: not at "secure,"
but at "honest about what's left."

## Glossary

- **SSRF** — Server-Side Request Forgery: tricking a server into making requests to
  targets *it* can reach (internal services, cloud metadata) that the attacker
  can't.
- **DNS rebinding** — resolving a name to a public IP at check time, then flipping
  it to an internal IP before the connection; defeats a check that isn't pinned to
  the validated address.
- **OAuth mix-up** — redeeming an authorization code at the wrong authorization
  server; defended by the RFC 9207 `iss` response parameter.
- **`iss`** — the authorization-response parameter naming the issuer that produced
  the callback; must be present and checked.
- **Session fixation** — an attacker fixing a victim's session id before login so
  the attacker's known id becomes authenticated; defeated by `cycle_key()`.
- **Rotating refresh token** — a single-use refresh token that returns a new one
  and invalidates itself; concurrent use requires a row lock.
- **URL namespace** — a prefix (`mosaic:`) qualifying URL names so a reusable app
  can't shadow the host project's global names.
- **Handle vs DID** — the reassignable label vs. the permanent identifier; trust
  decisions key on the DID.
- **`wantedDids`** — Jetstream's subscription filter; empty means *the whole
  firehose*, not *nothing*.
