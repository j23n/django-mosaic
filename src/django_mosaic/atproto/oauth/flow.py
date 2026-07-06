"""The ATProto OAuth authorization flow.

PDS → protected-resource metadata → authorization-server metadata → PAR
(PKCE S256, ``private_key_jwt`` client assertion, DPoP) → redirect →
callback code exchange → DPoP-bound tokens persisted as an
:class:`~..models.OAuthSession`. Every HTTP step that talks to an
authorization server or PDS handles the ``use_dpop_nonce`` dance: servers
may reject the first proof and hand back a ``DPoP-Nonce`` header, in which
case the request is retried once with the nonce embedded in a fresh proof.
"""

import base64
import hashlib
import logging
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from django.utils import timezone

from .. import client as atclient
from .. import conf
from .. import identity as identity_mod
from ..models import OAuthSession
from . import dpop, keys, metadata

logger = logging.getLogger("django_mosaic.atproto")

# Django-session key holding the pending authorization request between the
# redirect out and the callback.
PENDING_KEY = "mosaic_atproto_oauth_pending"
# Django-session key holding the DID of the signed-in visitor.
DID_KEY = "mosaic_atproto_oauth_did"

# Refresh access tokens this long before they actually expire.
EXPIRY_SLACK = timedelta(seconds=60)


class OAuthError(Exception):
    """Raised when any step of the OAuth flow fails."""


# --- discovery ---------------------------------------------------------------


def _get_json(url):
    resp = requests.get(url, timeout=conf.get_setting("TIMEOUT"))
    if resp.status_code >= 400:
        raise OAuthError(f"GET {url} failed: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as e:
        raise OAuthError(f"GET {url} returned non-JSON") from e


def authorization_server_for(pds_url):
    """The authorization server (issuer URL) protecting a PDS."""
    data = _get_json(f"{pds_url.rstrip('/')}/.well-known/oauth-protected-resource")
    servers = data.get("authorization_servers") or []
    if not servers:
        raise OAuthError(f"No authorization_servers advertised by {pds_url}")
    return servers[0].rstrip("/")


def authorization_server_metadata(issuer):
    """The issuer's OAuth metadata, with the issuer-match check the spec requires."""
    data = _get_json(f"{issuer}/.well-known/oauth-authorization-server")
    if data.get("issuer", "").rstrip("/") != issuer:
        raise OAuthError(f"Issuer mismatch in metadata from {issuer}")
    for field in ("authorization_endpoint", "token_endpoint"):
        if not data.get(field):
            raise OAuthError(f"Authorization server metadata missing {field}")
    return data


# --- request plumbing ---------------------------------------------------------


def _client_assertion(audience):
    """A private_key_jwt assertion authenticating this client to `audience`."""
    key = keys.load_client_key()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    now = int(time.time())
    return jwt.encode(
        {
            "iss": metadata.client_id(),
            "sub": metadata.client_id(),
            "aud": audience,
            "jti": secrets.token_urlsafe(16),
            "iat": now,
            "exp": now + 300,
        },
        pem,
        algorithm="ES256",
        headers={"kid": conf.oauth_client()["KEY_ID"]},
    )


def _wants_dpop_nonce(resp):
    if "DPoP-Nonce" not in resp.headers:
        return False
    if resp.status_code not in (400, 401):
        return False
    try:
        error = resp.json().get("error", "")
    except ValueError:
        error = ""
    # Resource servers signal via WWW-Authenticate instead of a JSON body.
    return error == "use_dpop_nonce" or "use_dpop_nonce" in resp.headers.get(
        "WWW-Authenticate", ""
    )


def _post_with_dpop(url, data, dpop_jwk, nonce=None):
    """Form-POST with a DPoP proof, retrying once if a nonce is demanded.

    Returns (json, nonce) so callers can reuse the server-issued nonce.
    """
    for attempt in range(2):
        resp = requests.post(
            url,
            data=data,
            headers={"DPoP": dpop.proof(dpop_jwk, "POST", url, nonce=nonce)},
            timeout=conf.get_setting("TIMEOUT"),
        )
        if attempt == 0 and _wants_dpop_nonce(resp):
            nonce = resp.headers["DPoP-Nonce"]
            continue
        break
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        raise OAuthError(f"POST {url} failed ({resp.status_code}): {detail}")
    return resp.json(), resp.headers.get("DPoP-Nonce", nonce)


# --- the flow -----------------------------------------------------------------


def start_auth(request, handle):
    """Begin the flow for `handle`; returns the authorize URL to redirect to.

    Stores the pending request (state, PKCE verifier, DPoP key, expected DID)
    in the visitor's Django session for :func:`complete_auth`.
    """
    identity = identity_mod.resolve(handle)
    atclient._validate_pds_url(identity.pds_url)
    issuer = authorization_server_for(identity.pds_url)
    server = authorization_server_metadata(issuer)
    par_endpoint = server.get("pushed_authorization_request_endpoint")
    if not par_endpoint:
        raise OAuthError(f"{issuer} does not support PAR (required by atproto)")

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    dpop_jwk = dpop.generate_key()

    par, nonce = _post_with_dpop(
        par_endpoint,
        {
            "client_id": metadata.client_id(),
            "response_type": "code",
            "redirect_uri": metadata.redirect_uri(),
            "scope": conf.oauth_client()["SCOPE"],
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "login_hint": identity.handle,
            "client_assertion_type": (
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            ),
            "client_assertion": _client_assertion(issuer),
        },
        dpop_jwk,
    )

    request.session[PENDING_KEY] = {
        "state": state,
        "verifier": verifier,
        "dpop_jwk": dpop_jwk,
        "dpop_nonce": nonce,
        "issuer": issuer,
        "token_endpoint": server["token_endpoint"],
        "did": identity.did,
        "handle": identity.handle,
        "pds_url": identity.pds_url,
    }
    query = urlencode(
        {"client_id": metadata.client_id(), "request_uri": par["request_uri"]}
    )
    return f"{server['authorization_endpoint']}?{query}"


def complete_auth(request):
    """Handle the callback: exchange the code and persist the session.

    Returns the saved :class:`OAuthSession` and marks the visitor's Django
    session as signed in.
    """
    params = request.GET
    if params.get("error"):
        raise OAuthError(
            f"Authorization failed: {params['error']} "
            f"({params.get('error_description', 'no description')})"
        )
    pending = request.session.pop(PENDING_KEY, None)
    if not pending:
        raise OAuthError("No pending authorization request in this session.")
    if not secrets.compare_digest(params.get("state", ""), pending["state"]):
        raise OAuthError("State mismatch — possible CSRF, aborting.")
    # atproto requires the `iss` callback param; reject a swapped issuer.
    if params.get("iss", pending["issuer"]).rstrip("/") != pending["issuer"]:
        raise OAuthError("Issuer mismatch in callback.")
    code = params.get("code")
    if not code:
        raise OAuthError("Callback is missing the authorization code.")

    tokens, nonce = _post_with_dpop(
        pending["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": metadata.redirect_uri(),
            "code_verifier": pending["verifier"],
            "client_id": metadata.client_id(),
            "client_assertion_type": (
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            ),
            "client_assertion": _client_assertion(pending["issuer"]),
        },
        pending["dpop_jwk"],
        nonce=pending.get("dpop_nonce"),
    )

    if tokens.get("sub") != pending["did"]:
        raise OAuthError(
            f"Token subject {tokens.get('sub')!r} is not the expected account "
            f"{pending['did']!r}."
        )

    session, _ = OAuthSession.objects.update_or_create(
        did=pending["did"],
        defaults={
            "handle": pending["handle"],
            "pds_url": pending["pds_url"],
            "auth_server": pending["issuer"],
            "token_endpoint": pending["token_endpoint"],
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", conf.oauth_client()["SCOPE"]),
            "dpop_jwk": pending["dpop_jwk"],
            "access_token_expires_at": _expiry(tokens),
        },
    )
    request.session[DID_KEY] = session.did
    return session


def _expiry(tokens):
    expires_in = tokens.get("expires_in")
    if not expires_in:
        return None
    return timezone.now() + timedelta(seconds=int(expires_in))


def refresh(session):
    """Refresh the session's access token in place; returns the session."""
    if not session.refresh_token:
        raise OAuthError(f"No refresh token stored for {session.did}.")
    tokens, _ = _post_with_dpop(
        session.token_endpoint,
        {
            "grant_type": "refresh_token",
            "refresh_token": session.refresh_token,
            "client_id": metadata.client_id(),
            "client_assertion_type": (
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            ),
            "client_assertion": _client_assertion(session.auth_server),
        },
        session.dpop_jwk,
    )
    session.access_token = tokens["access_token"]
    if tokens.get("refresh_token"):
        session.refresh_token = tokens["refresh_token"]
    session.access_token_expires_at = _expiry(tokens)
    session.save(
        update_fields=["access_token", "refresh_token", "access_token_expires_at"]
    )
    return session


# --- authenticated XRPC --------------------------------------------------------


def current_session(request):
    """The signed-in visitor's OAuthSession, or None."""
    did = request.session.get(DID_KEY)
    if not did:
        return None
    return OAuthSession.objects.filter(did=did).first()


def logout(request):
    """Sign the visitor out (keeps the stored tokens for server-side use)."""
    request.session.pop(DID_KEY, None)
    request.session.pop(PENDING_KEY, None)


def xrpc_call(session, nsid, method="GET", params=None, json_body=None):
    """Authenticated XRPC against the session's PDS with DPoP.

    Refreshes the access token when it is (about to be) expired, replays the
    request once on a DPoP-nonce demand, and once more after a refresh if the
    PDS still reports the token invalid.
    """
    if (
        session.access_token_expires_at
        and session.access_token_expires_at <= timezone.now() + EXPIRY_SLACK
        and session.refresh_token
    ):
        refresh(session)

    url = f"{session.pds_url.rstrip('/')}/xrpc/{nsid}"
    nonce = session.dpop_pds_nonce or None
    refreshed = False
    for _ in range(3):
        headers = {
            "Authorization": f"DPoP {session.access_token}",
            "DPoP": dpop.proof(
                session.dpop_jwk,
                method,
                url,
                nonce=nonce,
                access_token=session.access_token,
            ),
        }
        resp = requests.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=conf.get_setting("TIMEOUT"),
        )
        if _wants_dpop_nonce(resp):
            nonce = resp.headers["DPoP-Nonce"]
            continue
        if resp.status_code == 401 and session.refresh_token and not refreshed:
            refresh(session)
            refreshed = True
            continue
        break

    if resp.headers.get("DPoP-Nonce") and resp.headers["DPoP-Nonce"] != (
        session.dpop_pds_nonce or None
    ):
        session.dpop_pds_nonce = resp.headers["DPoP-Nonce"]
        session.save(update_fields=["dpop_pds_nonce"])

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        raise OAuthError(f"XRPC {nsid} failed ({resp.status_code}): {detail}")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()
