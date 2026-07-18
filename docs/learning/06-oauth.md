# PR 6 — The ATProto OAuth client (`django_mosaic.atproto.oauth`)

> **Stack:** 6/12 · **base:** `learn/05-preview`
> **Commit:** `02735a0` · **What it adds:** a full ATProto OAuth client — a
> confidential client using `private_key_jwt` + a published JWKS, PAR, PKCE
> S256, and DPoP-bound tokens — so any visitor can **sign in with their own
> ATProto account** and mosaic can act as them over XRPC.

## The one-sentence version

A visitor types their handle; mosaic resolves it to a PDS, discovers that PDS's
authorization server, **pushes** the authorization request (PAR) signed with the
client's private key and bound to a PKCE verifier + a fresh **DPoP** key, sends
the visitor off to approve, then exchanges the returned code for
**sender-constrained** access/refresh tokens that only work when accompanied by a
per-request DPoP proof signed with that same key — all persisted as one
`OAuthSession` row per DID.

## Learning objectives

**ATProto**

- Understand why ATProto OAuth stacks **four** mechanisms and what attack each
  one closes: **PAR** (the request never rides in a tamperable front-channel
  URL), **PKCE** (a stolen `code` is useless without the verifier), **DPoP**
  (a stolen *token* is useless without the key), and **`private_key_jwt`**
  (the client authenticates by signature, so there is no shared secret to leak).
- Know what **DPoP** actually is on the wire: a short-lived JWT, signed by a
  per-session key, carrying `htm`/`htu`/`jti`/`iat` (and `ath` on resource
  calls), plus the **nonce dance** — the `401`/`400 use_dpop_nonce` reply and
  the one-shot retry with the server's `DPoP-Nonce` folded into a fresh proof.
- Understand **`private_key_jwt`** vs a client secret: asymmetric client auth
  where you *publish* a JWKS and *sign* a client assertion, so the credential
  never crosses the wire.
- Trace the **metadata discovery chain**: PDS → protected-resource metadata →
  authorization-server metadata, with the **issuer-match** check the spec
  demands.
- Know why the `iss` callback parameter matters — **mix-up defense** (RFC 9207)
  — and why the `sub` of the returned token must equal the DID you started with.

**Python / Django**

- The `cryptography` + `PyJWT` stack for **ES256 (P-256)**: generating keys,
  serializing them as **JWK** and PEM, and signing JWTs (client assertions and
  DPoP proofs).
- Serving a **JWKS** document that publishes *only* the public half of a key.
- Storing DPoP-bound tokens durably (`OAuthSession`, one row per DID) and
  refreshing a **rotating** refresh token — and the concurrency hazard that a
  **row lock** (`select_for_update`) exists to solve, which this PR does *not*
  yet close.
- Wiring routes **only when configured** (import-time gating on
  `conf.oauth_enabled()`), behind an optional `oauth` extra.

## Grounding: official docs

The code is a direct implementation of these; read the atproto profile first,
then reach for the RFCs when you want the "why."

- ATProto OAuth profile — <https://atproto.com/specs/oauth>
- Bluesky OAuth client guide — <https://docs.bsky.app/docs/advanced-guides/oauth-client>
- **DPoP** (sender-constrained tokens) — RFC 9449,
  <https://www.rfc-editor.org/rfc/rfc9449>
- **PAR** (pushed authorization requests) — RFC 9126,
  <https://www.rfc-editor.org/rfc/rfc9126>
- **PKCE** — RFC 7636, <https://www.rfc-editor.org/rfc/rfc7636>
- **`private_key_jwt`** / JWT client authentication — RFC 7523,
  <https://www.rfc-editor.org/rfc/rfc7523>
- **JWK / JWKS** — RFC 7517, <https://www.rfc-editor.org/rfc/rfc7517>
- **Mix-up defense / the `iss` parameter** — RFC 9207,
  <https://www.rfc-editor.org/rfc/rfc9207>
- PyJWT — <https://pyjwt.readthedocs.io/> · cryptography —
  <https://cryptography.io/>
- Django `select_for_update` —
  <https://docs.djangoproject.com/en/stable/ref/models/querysets/#select-for-update>
- DID / handle — <https://atproto.com/specs/did>

## Background: the model this PR implements

PR 1 wrote to *your own* repo with an app password — a two-line
`createSession`. That is fine for a single owner. This PR is the write side for
**other people**: a visitor authenticates with their own account so mosaic can,
later, claim a site or write on their behalf. App passwords cannot do that
safely across users; OAuth can.

ATProto's OAuth profile is deliberately strict. A plain OAuth code flow has two
classic thefts:

- **Steal the `code`** off the callback URL (referer leak, open redirect, shared
  device) and redeem it yourself.
- **Steal the access token** (a proxy log, an SSRF, a compromised downstream) and
  replay it as a bearer.

ATProto closes both, plus the client-secret problem, by requiring four things
together. Keep this table in mind — the whole PR is these four rows:

```
PKCE (RFC 7636)         binds the code to a secret verifier    -> stolen code is inert
DPoP (RFC 9449)         binds every token to a proof key       -> stolen token is inert
PAR  (RFC 9126)         the request is pushed server-to-server -> front channel can't be tampered
private_key_jwt (7523)  client auth is a signature, no secret  -> nothing shared to leak
```

There is no client *registration* step. In ATProto the **client_id is a URL** —
`<BASE_URL>/oauth/client-metadata.json` — and the authorization server fetches
that document to learn who you are and how you authenticate. So "being a client"
means *serving two JSON documents* (`client-metadata.json` and `jwks.json`) and
holding one private key. That is why `metadata.py` and `keys.py` exist before any
flow code does.

The discovery chain mirrors the read-side resolution from PR 1, one hop longer:

```
handle  --(identity.resolve, from PR 1/5)-->  DID + PDS URL
PDS     --/.well-known/oauth-protected-resource-->  authorization_servers[0]  (the issuer)
issuer  --/.well-known/oauth-authorization-server-->  authorization + token + PAR endpoints
```

## Guided tour of the diff (read in this order)

The flow crosses six new files. Read them in dependency order — config, then the
three "primitives" modules (metadata, keys, dpop), then `flow.py` which composes
them, then the views/urls/model that expose it.

### 1. `atproto/conf.py` — the config surface (as in PR 1)

One new sub-dict on the existing `MOSAIC_ATPROTO` settings, plus two helpers:

```python
"OAUTH_CLIENT": {
    "BASE_URL": "",       # public https origin; the client_id derives from it
    "PRIVATE_KEY": "",    # PEM ES256 (P-256) confidential-client key
    "KEY_ID": "mosaic-oauth-1",
    "SCOPE": "atproto transition:generic",
},
```

```python
def oauth_client():
    return {**DEFAULTS["OAUTH_CLIENT"], **(get_setting("OAUTH_CLIENT") or {})}

def oauth_enabled():
    client = oauth_client()
    return bool(client["BASE_URL"] and client["PRIVATE_KEY"])
```

Same **fail-closed-to-a-no-op** discipline as PR 1's `enabled()`: without a
`BASE_URL` *and* a `PRIVATE_KEY` the whole OAuth surface is inert — no routes,
no key loads. Note `BASE_URL` must be a **real internet-reachable https origin**
(no `localhost`), because the authorization server has to fetch your metadata.

### 2. `oauth/metadata.py` — the document that *is* your client_id

Pure functions over `BASE_URL`:

```python
def client_id():    return f"{base_url()}/oauth/client-metadata.json"
def redirect_uri(): return f"{base_url()}/oauth/callback"
def jwks_uri():     return f"{base_url()}/oauth/jwks.json"
```

`client_metadata()` is the JSON the auth server fetches. The load-bearing fields
are the ones that *declare your security posture* and are validated against your
actual behavior:

```python
"dpop_bound_access_tokens": True,
"token_endpoint_auth_method": "private_key_jwt",
"token_endpoint_auth_signing_alg": "ES256",
"jwks_uri": jwks_uri(),
"grant_types": ["authorization_code", "refresh_token"],
"response_types": ["code"],
"redirect_uris": [redirect_uri()],
```

If you claim `private_key_jwt` here but send a client secret, or claim
`dpop_bound_access_tokens` but omit the proof, the server rejects you. The
metadata document is a *contract you must then honour on every request*.

### 3. `oauth/keys.py` — the confidential-client key + JWKS

One long-lived ES256 key. `load_client_key()` reads the PEM from settings and
**validates the curve** (`SECP256R1`, i.e. P-256) — a wrong-curve key is a
config error, not a runtime 500 later. `generate_private_key_pem()` backs the
`manage.py atproto oauth-key` command (§10).

`public_jwk(key, kid)` turns a `cryptography` EC key into the JWK dict — note the
coordinates are big-endian, fixed 32-byte, base64url **without padding**
(`_b64url_uint`), which is exactly the JWK encoding RFC 7517 mandates:

```python
{"kty": "EC", "crv": "P-256", "x": ..., "y": ..., "use": "sig", "alg": "ES256"}
```

`client_jwks()` wraps it as `{"keys": [ ... ]}` with the `kid`. Crucially it
calls `public_jwk` on the **public** numbers only — the private scalar `d` never
appears. That is the whole point of a JWKS: publish `x`/`y`, keep `d`.

### 4. `oauth/dpop.py` — per-session keys and proof JWTs

DPoP keys are a *different* key from the client key: **short-lived, one per user
session**, generated at `start_auth` and stored on the `OAuthSession` row so
every later call for that user re-signs with the same key.

- `generate_key()` mints a P-256 key and serializes it as a **private** JWK dict
  (adds `d`, strips `use`/`alg`). This dict is what lands in the DB
  (`dpop_jwk`, a `JSONField`).
- `_load_private_jwk(jwk)` reverses that back into a `cryptography` key for
  signing.
- `proof(jwk, method, url, nonce=None, access_token=None)` builds one DPoP proof
  — covered end to end in **Deep dive 1** below.

### 5. `oauth/flow.py` — the flow (read this whole file)

384 lines, four phases. Trace them in call order.

**Discovery.** `authorization_server_for(pds_url)` fetches
`/.well-known/oauth-protected-resource` and returns `authorization_servers[0]`
— the **issuer**. `authorization_server_metadata(issuer)` fetches
`/.well-known/oauth-authorization-server` and enforces the spec's
**issuer-match** invariant before trusting anything in it:

```python
if data.get("issuer", "").rstrip("/") != issuer:
    raise OAuthError(f"Issuer mismatch in metadata from {issuer}")
```

**Client assertion.** `_client_assertion(audience)` is the `private_key_jwt`: a
JWT with `iss == sub == client_id()`, `aud` set to the endpoint's issuer, a
random `jti`, and a 5-minute expiry, signed **ES256** with the client key and
carrying the `kid` in the header so the server knows which published JWK to
verify against. This is what proves "I am the client whose metadata you fetched"
— no secret transmitted (RFC 7523).

**Start.** `start_auth(request, handle)`:

1. `identity.resolve(handle)` → `(handle, did, pds_url)` (reuses PR 1/5), then
   `atclient._validate_pds_url(...)` (reuses PR 5's SSRF guard).
2. Discover issuer + metadata; require a `pushed_authorization_request_endpoint`
   (ATProto **mandates** PAR — no PAR endpoint is a hard error).
3. Generate `state` (CSRF/lookup token), a PKCE `verifier`, its S256
   `challenge`, and a fresh DPoP key.
4. `_post_with_dpop(par_endpoint, {...}, dpop_jwk)` — the **PAR** call. The body
   carries `client_id`, `redirect_uri`, `scope`, `state`, `code_challenge` +
   `code_challenge_method=S256`, `login_hint`, and the `client_assertion`. The
   server stores it and returns a `request_uri`.
5. Stash **everything the callback will need** in the Django session under
   `PENDING_KEY` — `state`, `verifier`, `dpop_jwk`, `dpop_nonce`, `issuer`,
   `token_endpoint`, `did`, `handle`, `pds_url`.
6. Redirect the visitor to
   `authorization_endpoint?client_id=...&request_uri=...`. Notice how *little*
   is in this front-channel URL: just a pointer (`request_uri`) to the request
   the server already has. That is PAR's payoff — nothing sensitive to tamper
   with in the browser.

**Callback.** `complete_auth(request)`:

1. Surface any `error` param.
2. Pop `PENDING_KEY`; if absent, abort (no dangling exchange).
3. `secrets.compare_digest(state)` — constant-time CSRF check.
4. **`iss` check** — reject a swapped issuer in the callback (mix-up defense).
5. `_post_with_dpop(token_endpoint, {grant_type=authorization_code, code,
   code_verifier, client_assertion, ...}, dpop_jwk, nonce=...)` — the code
   exchange. The `code_verifier` is what makes a stolen `code` useless (PKCE).
6. **`sub` check** — `tokens["sub"]` must equal the DID we resolved at the
   start; otherwise the account that authorized is not the one we asked for:

   ```python
   if tokens.get("sub") != pending["did"]:
       raise OAuthError(...)
   ```
7. `OAuthSession.objects.update_or_create(did=..., defaults={...})` — persist the
   DPoP-bound tokens, the DPoP JWK, the endpoints, and the computed expiry. One
   row per DID: **signing in again replaces the grant.**

**Authenticated XRPC.** `xrpc_call(session, nsid, ...)` is the payoff — issue an
XRPC as the signed-in user. Expiry-triggered refresh, DPoP proof, the nonce
retry, and a `401`-triggered refresh-and-retry — covered in **Deep dive 2**.

> **Review question.** `authorization_server_for` returns
> `servers[0].rstrip("/")` — the *first* advertised authorization server, with
> no allowlist. What stops a malicious PDS from advertising an attacker-run
> issuer? (Answer: not much, *here*. The `iss`/`sub`/`state` checks stop
> **cross-server mix-up**, but a user who points mosaic at a hostile PDS is
> trusting that PDS — which, for "sign in with *your* account," is the user's own
> server, so the trust is appropriate. The real exposure is SSRF on the discovery
> `GET`s; `_validate_pds_url` guards the PDS hop but the issuer URLs are fetched
> raw by `_get_json`. Keep it in mind — it rhymes with PR 1's redirect hole and
> is exactly the class PR 12 hardens.)

### 6. `oauth/views.py` + `urls.py` — the four routes and two documents

Two public documents and three flow endpoints:

- `client_metadata` / `jwks` — the JSON the auth server fetches (§2, §3).
- `login` — GET renders `oauth-login.html`; POST normalizes the handle
  (`strip().lstrip("@").lower()`), stores an open-redirect-safe `next`, and
  `redirect(flow.start_auth(...))`.
- `callback` — the `redirect_uri`; runs `complete_auth`, then redirects to the
  saved `next`.
- `logout` — `@require_POST`; clears the session keys (but **keeps** the stored
  tokens for server-side use — revocation is "delete the row").

`_safe_next` is the open-redirect guard: Django's
`url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()},
require_https=request.is_secure())`, falling back to `"/"`. Any off-host or
downgraded `next` is dropped.

In `urls.py`, the routes are added **at import time, only if `oauth_enabled()`**,
and the import of the oauth package is wrapped so a misconfiguration (extra not
installed but `OAUTH_CLIENT` set) raises a clear `ImproperlyConfigured` rather
than a vague `ImportError`:

```python
if conf.oauth_enabled():
    try:
        from .oauth import views as oauth_views
    except ImportError as exc:
        raise ImproperlyConfigured("... install django-mosaic[oauth].") from exc
    urlpatterns += [ path("oauth/client-metadata.json", ...), ... ]
```

### 7. `atproto/models.py` + migration `0003` — `OAuthSession`

One row per DID (`did` is `unique`). It stores the endpoints (`pds_url`,
`auth_server`, `token_endpoint`), the secrets (`access_token`, `refresh_token`,
`dpop_jwk` as JSON), the PDS-side DPoP nonce cache (`dpop_pds_nonce`), and
`access_token_expires_at`. The docstring is blunt: **these are secrets — anyone
who can read this table can act as the account.** That framing drives the admin.

### 8. `atproto/admin.py` — read-only, token-free

`OAuthSessionAdmin` shows *who* is connected (`handle`, `did`, `scope`, expiry)
but the `fields`/`readonly_fields` deliberately **exclude every token and the
DPoP key**, and `has_add_permission` returns `False`. You can see and revoke
(delete the row) but never read or forge token material through the admin.

### 9. `templates/atproto/oauth-login.html`

A standalone, `noindex` handle-entry form (override it for your branding, per
the project's CLAUDE.md note that templates are illustrative). It flips between
"signed in as X / sign out" and the handle form, threads `next` through as a
hidden field, and shows flow errors.

### 10. `management/commands/atproto.py` — `oauth-key`

Adds an `oauth-key` subcommand that prints a fresh ES256 PEM to **stdout only**
(nothing written to disk/DB) with the ImportError→CommandError guard for the
missing extra. The docstring tells you to store it in an env var / secret
manager and reference it from settings — never commit the key.

### 11. `tests/test_atproto_oauth.py`

35 tests, all HTTP mocked (`mock.patch.object(flow, "requests")`), and the whole
module is `pytest.importorskip("jwt")`/`"cryptography"` — it *skips cleanly* when
the extra isn't installed, mirroring the deploy-extra pattern. The interesting
ones to read: `test_dpop_nonce_retry`, `test_xrpc_nonce_retry_and_persist`,
`test_xrpc_retries_once_after_401_refresh`, `test_sub_mismatch_rejected`,
`test_issuer_mismatch_rejected`, and `test_jwks_publishes_public_key_only`. They
assert on *payload shape and control flow*, not live responses — the same
discipline as PR 1.

## Deep dive 1: DPoP end to end

DPoP (Demonstrating Proof-of-Possession, RFC 9449) turns a **bearer** token
("whoever holds it, wins") into a **sender-constrained** token ("you also have to
prove you hold the key it was bound to"). ATProto requires it on every token
request and every authenticated XRPC call. Three pieces make it work here.

### The proof JWT (`dpop.proof`)

```python
headers = {"typ": "dpop+jwt", "jwk": public_part(jwk)}   # embeds the PUBLIC key
claims  = {
    "jti": secrets.token_urlsafe(16),           # unique -> replay detection
    "htm": method.upper(),                       # HTTP method it's valid for
    "htu": url.split("#",1)[0].split("?",1)[0],  # HTTP URL, query/fragment stripped
    "iat": int(time.time()),                     # freshness
}
if nonce: claims["nonce"] = nonce                # server-issued, see below
if access_token:                                 # only on resource (XRPC) calls
    digest = hashlib.sha256(access_token.encode()).digest()
    claims["ath"] = base64url(digest)            # binds the proof to *this* token
```

Read the claims as answers to "why can't I replay this proof?":

- **`jwk` in the header** — the proof carries its *own* public key. The server
  binds the issued token to a hash of this key (the JWK thumbprint) and, on every
  later request, checks the proof is signed by the matching private key. The
  client key never moves; only signatures do.
- **`htm` + `htu`** — the proof is valid for exactly one method + URL. A proof
  captured on a `GET` to endpoint A cannot be replayed on a `POST` to endpoint B.
  (Note the deliberate `htu` normalization: strip fragment *and* query, because
  the spec's `htu` is the URL without them.)
- **`jti` + `iat`** — a unique id and a timestamp let the server reject replays
  and stale proofs within a short window.
- **`ath`** on resource calls — `base64url(sha256(access_token))`. This *ties the
  proof to the specific access token*, so a proof lifted from one request can't
  be paired with a *different* stolen token. On the resource call the token rides
  in `Authorization: DPoP <token>` (not `Bearer`), and the DPoP header carries
  the matching proof. Token-theft alone buys nothing: the thief lacks the private
  key to mint a proof with the right `ath`.

### The nonce dance (401/400 → retry)

Servers don't fully trust a client-chosen `iat` for freshness; they can demand a
**server-issued nonce** and refuse the first proof that lacks it. The detector:

```python
def _wants_dpop_nonce(resp):
    if "DPoP-Nonce" not in resp.headers:        return False
    if resp.status_code not in (400, 401):      return False
    error = (resp.json().get("error","") if json else "")
    return error == "use_dpop_nonce" or "use_dpop_nonce" in \
           resp.headers.get("WWW-Authenticate", "")
```

Two shapes are handled because authorization servers signal in a **JSON body**
(`{"error": "use_dpop_nonce"}`) while resource servers signal in the
**`WWW-Authenticate`** header — same meaning, different envelope.

The retry, for token-endpoint calls, is `_post_with_dpop`:

```python
for attempt in range(2):
    resp = requests.post(url, data=data,
        headers={"DPoP": dpop.proof(dpop_jwk, "POST", url, nonce=nonce)}, ...)
    if attempt == 0 and _wants_dpop_nonce(resp):
        nonce = resp.headers["DPoP-Nonce"]   # grab it, loop once
        continue
    break
...
return resp.json(), resp.headers.get("DPoP-Nonce", nonce)   # hand the nonce back
```

The first request gambles that no nonce is needed; if the server says
`use_dpop_nonce`, the second request re-mints the proof **with the nonce baked
in** and succeeds. The function returns the freshest nonce so the caller can
carry it forward — this is why `start_auth` stashes `dpop_nonce` in the session:
the PAR call's nonce primes the token exchange, avoiding a wasted round trip
there.

For the *resource* side, `xrpc_call` runs its own version of the loop (up to
three passes) and **persists** the nonce so subsequent calls skip the warm-up:

```python
nonce = session.dpop_pds_nonce or None
for _ in range(3):
    resp = requests.request(method, url, headers={
        "Authorization": f"DPoP {session.access_token}",
        "DPoP": dpop.proof(session.dpop_jwk, method, url,
                           nonce=nonce, access_token=session.access_token)}, ...)
    if _wants_dpop_nonce(resp):
        nonce = resp.headers["DPoP-Nonce"]; continue
    if resp.status_code == 401 and session.refresh_token and not refreshed:
        refresh(session); refreshed = True; continue
    break
# cache the newest PDS nonce for next time
if resp.headers.get("DPoP-Nonce") and resp.headers["DPoP-Nonce"] != (session.dpop_pds_nonce or None):
    session.dpop_pds_nonce = resp.headers["DPoP-Nonce"]
    session.save(update_fields=["dpop_pds_nonce"])
```

Three exit paths braided together: nonce demand → retry with nonce; `401` with a
usable refresh token → refresh once and retry; anything else → done. The bounded
loop guarantees termination (`refreshed` flips true so you can't refresh twice),
and caching `dpop_pds_nonce` means steady-state traffic is one round trip, not
two.

## Deep dive 2: refreshing a rotating token — and the row lock that isn't here yet

ATProto refresh tokens **rotate**: each successful refresh may return a *new*
refresh token and invalidate the old one (rotation is how the server detects
theft — if an old refresh token is ever reused, something has been replayed, and
a strict server can revoke the *whole* grant). `refresh()` handles the rotation:

```python
def refresh(session):
    if not session.refresh_token:
        raise OAuthError(...)
    tokens, _ = _post_with_dpop(session.token_endpoint, {
        "grant_type": "refresh_token",
        "refresh_token": session.refresh_token,
        "client_id": metadata.client_id(),
        "client_assertion_type": "...jwt-bearer",
        "client_assertion": _client_assertion(session.auth_server),
    }, session.dpop_jwk)               # same per-session DPoP key
    session.access_token = tokens["access_token"]
    if tokens.get("refresh_token"):    # <-- store the rotated token
        session.refresh_token = tokens["refresh_token"]
    session.access_token_expires_at = _expiry(tokens)
    session.save(update_fields=["access_token","refresh_token","access_token_expires_at"])
    return session
```

`xrpc_call` calls it in two situations: **proactively** when the token is within
`EXPIRY_SLACK` (60 s) of expiring, and **reactively** on a `401`. Note the DPoP
proof on the refresh uses the *same* `session.dpop_jwk` — the refresh token is
bound to that key just like the access token; you must sign with it to refresh.

### The race this code is exposed to

Here is the sharp edge, and it is worth internalizing because it is invisible in
single-request testing. Imagine two requests for the **same DID** arriving
concurrently (two browser tabs, a burst of XRPC, two workers). Both read the same
`OAuthSession` row, both see the access token is expired, both call `refresh()`,
and both POST **the same `refresh_token`** to the server:

```
worker A                          worker B
--------                          --------
read session (rt = R0)            read session (rt = R0)
POST refresh(R0)  ---------------> server rotates: R0 -> R1  (R0 now invalid)
                   worker B: POST refresh(R0)  --> 400/401: R0 already used
save(rt=R1)                       ...and a strict server may now REVOKE the grant
```

Best case, worker B's refresh fails and its request 500s. **Worst case, the
server treats the reuse of `R0` as a stolen-token replay and revokes the entire
session** — the user is silently signed out and must re-authenticate. There is
also a lost-update flavor: two `save(update_fields=["refresh_token", ...])`
racing can clobber each other so the row ends up holding an already-invalidated
token.

**This PR does not guard against that.** `refresh()` does a plain read →
`session.save(update_fields=...)` with no locking and no re-check. Two concurrent
callers *can* both enter it. Flagging this is the point of the deep dive.

### The fix: serialize with `select_for_update`

The standard Django tool is a **row lock**: open a transaction, `SELECT ... FOR
UPDATE` the row so any concurrent transaction *blocks* until you commit, then —
critically — **re-read and re-check inside the lock**, so the second worker sees
the first worker's already-refreshed token and skips the network call entirely:

```python
from django.db import transaction

def refresh_locked(did):
    with transaction.atomic():
        session = OAuthSession.objects.select_for_update().get(did=did)
        # Re-check *under the lock*: did someone already refresh while we waited?
        if session.access_token_expires_at and \
           session.access_token_expires_at > timezone.now() + EXPIRY_SLACK:
            return session                     # fresh enough — don't burn the token
        return refresh(session)                # exactly one worker reaches here
```

`select_for_update()` issues `SELECT … FOR UPDATE` (Postgres/MySQL/Oracle;
it is a documented no-op on SQLite, which has no row locks — a real caveat for a
reusable app, see the Django docs). Worker A takes the lock, refreshes, commits;
worker B was **blocking on the same row**, and when it acquires the lock it
re-reads, sees a valid token, and returns without touching the network. The
rotating token is burned **exactly once**. That "block, then re-read and
double-check" shape is the canonical pattern for serializing an
expensive-and-single-use side effect keyed on a row — memorize it; it recurs
anywhere you cache-or-recompute under contention.

> **Review question.** Why re-check the expiry *inside* the lock instead of just
> locking and always refreshing? (Answer: without the re-check, every blocked
> worker still refreshes after acquiring the lock — you've serialized the burns
> but you're still doing N refreshes for N concurrent callers, each rotating the
> token again. The re-read collapses N refreshes to one: the lock orders them,
> the re-check makes all but the first a no-op.)

## Design decisions & "why not X"

- **Why a confidential client / `private_key_jwt`, not a public client?** A
  hosted mosaic *is* a server with a stable origin, so it can hold a private key
  and publish a JWKS. Confidential clients get refresh tokens and stronger
  guarantees. A pure public client (SPA, no backend secret) would be the choice
  only if there were nowhere to keep a key.
- **Why store the DPoP key per session in the DB, not one global DPoP key?** DPoP
  keys are meant to be scoped and rotatable; binding one key per `OAuthSession`
  limits blast radius (a leaked row compromises one user, not all) and matches
  "one grant per DID." The client `private_key_jwt` key is the opposite — one
  long-lived key, published — because it authenticates the *client*, not a user.
- **Why check both `iss` and `sub`?** They defend different attacks. `iss`
  (RFC 9207) is **mix-up defense** — a malicious authorization server can't get
  you to redeem a code at the wrong issuer. `sub == did` is **account-confusion
  defense** — the account that actually authorized must be the one you resolved
  from the typed handle. PR 12 hardens the `iss` handling further.
- **Why `update_or_create(did=...)` (one row per DID)?** DID is the immutable
  identity (handles are reassignable — the whole reason later PRs key on DID).
  Re-signing-in should *replace* the grant, not accumulate stale rows.
- **Why build routes at import time behind `oauth_enabled()`?** Same reason as
  PR 1's `enabled()`: an unconfigured install is completely inert, and a
  half-configured one (settings present, extra missing) fails **loudly and
  early** with `ImproperlyConfigured`, not with a confusing 500 on first request.
- **Why keep tokens after logout?** `logout` is a *browser* concern — it clears
  the Django session keys. The grant is a *server-side* asset mosaic may need to
  act on the user's behalf later. Revocation is an explicit, auditable act:
  delete the `OAuthSession` row (the only thing the admin lets you do).

## Exercises

1. **Read the proof.** Copy a `dpop.proof(...)` output into <https://jwt.io> (or
   `jwt.decode(..., options={"verify_signature": False})`). Identify `typ`, the
   embedded `jwk`, and `htm`/`htu`/`jti`/`iat`. Now call it with an
   `access_token` and confirm `ath == base64url(sha256(token))` by hand.
2. **Spot the concurrency bug.** Without looking back at Deep dive 2: two
   simultaneous `xrpc_call`s for the same DID both find an expired token. Walk
   through `refresh()` line by line and say exactly where the rotating refresh
   token gets double-spent. Then write the `select_for_update` version and argue
   why the *re-check inside the lock* is load-bearing.
3. **Trace the nonce.** Follow one `DPoP-Nonce` value from the PAR response
   (`_post_with_dpop` return) through the session's `dpop_nonce`, into the token
   exchange, and separately follow `dpop_pds_nonce` through `xrpc_call`. Why are
   these two nonces stored in different places?
4. **Break the metadata.** Flip `token_endpoint_auth_method` in
   `client_metadata()` to `"client_secret_post"` while the code still sends a
   `private_key_jwt` assertion. Which side detects the mismatch, and what does
   the spec say the auth server must do?
5. **Predict the SSRF.** `_get_json` fetches the issuer's well-known URLs with no
   IP validation, while the PDS hop goes through `_validate_pds_url`. Sketch how
   a hostile PDS advertising an internal `authorization_servers[0]` could turn
   into an internal `GET`. (This is the class PR 12 closes.)

## Verify it yourself

```bash
git checkout learn/06-oauth
pip install -e '.[oauth]'                          # pyjwt + cryptography
python -m pytest tests/test_atproto_oauth.py -q    # all HTTP mocked; skips w/o extra
python manage.py atproto oauth-key                 # prints a fresh ES256 PEM
git show 02735a0 -- src/django_mosaic/atproto/oauth/flow.py   # the 384-line core
```

Then, with `OAUTH_CLIENT` configured against a real origin, fetch
`<BASE_URL>/oauth/client-metadata.json` and `<BASE_URL>/oauth/jwks.json` and
confirm the JWKS contains **only** `x`/`y` (never `d`).

## Glossary

- **DPoP** — Demonstrating Proof-of-Possession (RFC 9449); a per-request signed
  JWT that binds a token to a key, so a stolen token can't be replayed.
- **DPoP proof** — the JWT itself: header `typ=dpop+jwt` + embedded public `jwk`,
  claims `htm`/`htu`/`jti`/`iat` (+ `nonce`, + `ath` on resource calls).
- **`ath`** — access-token hash claim; `base64url(sha256(access_token))`, ties a
  proof to one specific token.
- **DPoP-Nonce** — a server-issued freshness value; missing it yields
  `use_dpop_nonce` and a one-shot retry.
- **PAR** — Pushed Authorization Request (RFC 9126); the request is POSTed to the
  server first, which returns a `request_uri` used in the redirect.
- **PKCE** — Proof Key for Code Exchange (RFC 7636); a `verifier`/`challenge`
  (S256) pair that makes a stolen `code` useless.
- **`private_key_jwt`** — asymmetric client authentication (RFC 7523): the client
  signs an assertion; its public key is published in a JWKS.
- **JWK / JWKS** — JSON Web Key / Key Set (RFC 7517); the published public-key
  document at `/oauth/jwks.json`.
- **client_id (ATProto)** — a *URL* to the client-metadata document; there is no
  registration step.
- **Issuer** — the authorization server URL; must match its own metadata's
  `issuer` and the callback's `iss`.
- **ES256 / P-256** — ECDSA over the NIST P-256 (`secp256r1`) curve; the signing
  algorithm for both the client key and DPoP keys.
- **`select_for_update`** — Django's row lock (`SELECT … FOR UPDATE`); serializes
  concurrent access to a row (no-op on SQLite).
- **Rotating refresh token** — a refresh token replaced on each use; reusing an
  old one signals replay and can revoke the grant.
