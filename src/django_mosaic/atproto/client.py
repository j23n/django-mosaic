"""Minimal XRPC client for a single-account, app-password ATProto session.

Deliberately tiny: mosaic only needs identity resolution, record CRUD, and
blob upload against the *owner's* PDS. Reads of other repos (lexicon pages,
preview mode) also go through ``xrpc_get`` since those endpoints are
unauthenticated.
"""

import ipaddress
import logging
import socket
from urllib.parse import unquote, urlsplit

import requests

from . import conf

logger = logging.getLogger("django_mosaic.atproto")


class AtprotoError(Exception):
    """Raised when an XRPC call fails."""


def _ip_is_safe(ip):
    """True if `ip` is a public, routable address (not internal/reserved)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolves_to_public_ip(host):
    """True unless `host` resolves to an internal/reserved address.

    A name-based blocklist can't stop a public-looking hostname whose A/AAAA
    record points at an internal IP (e.g. ``pds.attacker.com`` → 169.254.169.254,
    or a ``.nip.io`` name), so we resolve and inspect every address. A host that
    does not resolve at all is *not* an SSRF risk — the request would fail to
    connect anyway — so resolution failure fails open. (This does not defend
    against active DNS rebinding, which would require pinning the connection to
    the validated address.)
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    return all(_ip_is_safe(info[4][0]) for info in infos)


def _validate_pds_url(url):
    """Reject PDS endpoints that could be used for SSRF.

    DID documents are attacker-controlled input once we resolve arbitrary
    handles (preview mode): a malicious document could point the "PDS" at an
    internal service. Require https on a public hostname that does not resolve
    to an internal address. Owner-configured PDS_URL overrides are trusted
    settings and bypass this (so a same-box http://localhost PDS still works
    for self-hosters).
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise AtprotoError(f"Refusing non-https PDS endpoint: {url}")
    host = parts.hostname or ""
    if not host or host == "localhost" or host.endswith((".local", ".internal")):
        raise AtprotoError(f"Refusing PDS endpoint host: {url}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # a hostname, not an IP literal — fine
    else:
        raise AtprotoError(f"Refusing IP-literal PDS endpoint: {url}")
    if not _resolves_to_public_ip(host):
        raise AtprotoError(f"Refusing PDS endpoint resolving to internal IP: {url}")
    return url


def _http_get(url, **kwargs):
    """``requests.get`` that never follows redirects.

    SSRF validation checks the URL we are about to fetch; following a redirect
    would let a validated public endpoint bounce the request to an internal
    host. XRPC/DID-document endpoints never legitimately redirect, so a 3xx is
    treated as an error by ``_raise_for_error``.
    """
    kwargs.setdefault("allow_redirects", False)
    return requests.get(url, **kwargs)


def _http_post(url, **kwargs):
    """``requests.post`` that never follows redirects (see :func:`_http_get`)."""
    kwargs.setdefault("allow_redirects", False)
    return requests.post(url, **kwargs)


def resolve_identity(handle):
    """Resolve a handle to (did, pds_url) via public directories.

    The configured DID/PDS_URL overrides apply only when resolving the site
    owner's own handle (so air-gapped or self-hosted setups skip network
    resolution) — never when resolving someone else's handle in preview mode.
    """
    is_owner = handle == conf.get_setting("HANDLE")
    did = conf.get_setting("DID") if is_owner else ""
    pds_url = conf.get_setting("PDS_URL") if is_owner else ""
    if did and pds_url:
        return did, pds_url.rstrip("/")

    timeout = conf.get_setting("TIMEOUT")
    if not did:
        resp = _http_get(
            "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": handle},
            timeout=timeout,
        )
        _raise_for_error(resp)
        did = resp.json()["did"]

    if not pds_url:
        pds_url = resolve_pds(did, timeout=timeout)

    return did, pds_url.rstrip("/")


def resolve_pds(did, timeout=None):
    """Resolve a DID to its PDS endpoint via its DID document.

    The endpoint is SSRF-validated (it comes from an attacker-controllable
    document once arbitrary DIDs are resolved).
    """
    if timeout is None:
        timeout = conf.get_setting("TIMEOUT")
    if did.startswith("did:plc:"):
        doc = _http_get(f"https://plc.directory/{did}", timeout=timeout)
    elif did.startswith("did:web:"):
        # The host is taken straight from an arbitrary DID, so the document
        # fetch itself is an SSRF vector — validate it, not just the endpoint
        # inside the returned document. Decode any percent-encoded port
        # (``%3A``) first so an internal IP can't hide behind the encoding.
        domain = unquote(did.removeprefix("did:web:"))
        doc_url = f"https://{domain}/.well-known/did.json"
        _validate_pds_url(doc_url)
        doc = _http_get(doc_url, timeout=timeout)
    else:
        raise AtprotoError(f"Unsupported DID method: {did}")
    _raise_for_error(doc)
    services = doc.json().get("service", [])
    pds = next(
        (
            s["serviceEndpoint"]
            for s in services
            if s.get("type") == "AtprotoPersonalDataServer"
        ),
        None,
    )
    if not pds:
        raise AtprotoError(f"No PDS endpoint in DID document for {did}")
    return _validate_pds_url(pds).rstrip("/")


def _raise_for_error(resp):
    if 300 <= resp.status_code < 400:
        raise AtprotoError(
            f"Refusing to follow redirect ({resp.status_code}) from "
            f"{resp.request.url} to {resp.headers.get('Location', '')}"
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text[:300]
        raise AtprotoError(f"XRPC error {resp.status_code}: {detail}")


class Session:
    """An authenticated app-password session against the owner's PDS."""

    def __init__(self, pds_url, did, access_jwt):
        self.pds_url = pds_url
        self.did = did
        self.access_jwt = access_jwt

    @classmethod
    def create(cls):
        handle = conf.get_setting("HANDLE")
        did, pds_url = resolve_identity(handle)
        resp = _http_post(
            f"{pds_url}/xrpc/com.atproto.server.createSession",
            json={
                "identifier": handle,
                "password": conf.get_setting("APP_PASSWORD"),
            },
            timeout=conf.get_setting("TIMEOUT"),
        )
        _raise_for_error(resp)
        data = resp.json()
        return cls(pds_url, data["did"], data["accessJwt"])

    def _headers(self):
        return {"Authorization": f"Bearer {self.access_jwt}"}

    def _post(self, nsid, payload):
        resp = _http_post(
            f"{self.pds_url}/xrpc/{nsid}",
            json=payload,
            headers=self._headers(),
            timeout=conf.get_setting("TIMEOUT"),
        )
        _raise_for_error(resp)
        return resp.json()

    def create_record(self, collection, record, rkey=None):
        payload = {"repo": self.did, "collection": collection, "record": record}
        if rkey:
            payload["rkey"] = rkey
        return self._post("com.atproto.repo.createRecord", payload)

    def put_record(self, collection, rkey, record):
        return self._post(
            "com.atproto.repo.putRecord",
            {
                "repo": self.did,
                "collection": collection,
                "rkey": rkey,
                "record": record,
            },
        )

    def delete_record(self, collection, rkey):
        return self._post(
            "com.atproto.repo.deleteRecord",
            {"repo": self.did, "collection": collection, "rkey": rkey},
        )

    def upload_blob(self, data, mime_type):
        resp = _http_post(
            f"{self.pds_url}/xrpc/com.atproto.repo.uploadBlob",
            data=data,
            headers={**self._headers(), "Content-Type": mime_type},
            timeout=conf.get_setting("TIMEOUT"),
        )
        _raise_for_error(resp)
        return resp.json()["blob"]


def xrpc_get(base_url, nsid, params=None, timeout=None):
    """Unauthenticated XRPC GET (public reads: listRecords, getPostThread...)."""
    resp = _http_get(
        f"{base_url.rstrip('/')}/xrpc/{nsid}",
        params=params or {},
        timeout=timeout if timeout is not None else conf.get_setting("TIMEOUT"),
    )
    _raise_for_error(resp)
    return resp.json()
