"""Minimal XRPC client for a single-account, app-password ATProto session.

Deliberately tiny: mosaic only needs identity resolution, record CRUD, and
blob upload against the *owner's* PDS. Reads of other repos (lexicon pages,
preview mode) also go through ``xrpc_get`` since those endpoints are
unauthenticated.
"""

import ipaddress
import logging
from urllib.parse import urlsplit

import requests

from . import conf

logger = logging.getLogger("django_mosaic.atproto")


class AtprotoError(Exception):
    """Raised when an XRPC call fails."""


def _validate_pds_url(url):
    """Reject PDS endpoints that could be used for SSRF.

    DID documents are attacker-controlled input once we resolve arbitrary
    handles (preview mode): a malicious document could point the "PDS" at an
    internal service. Require https on a public hostname. Owner-configured
    PDS_URL overrides are trusted settings and bypass this (so a same-box
    http://localhost PDS still works for self-hosters).
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
    return url


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
        resp = requests.get(
            "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle",
            params={"handle": handle},
            timeout=timeout,
        )
        _raise_for_error(resp)
        did = resp.json()["did"]

    if not pds_url:
        if did.startswith("did:plc:"):
            doc = requests.get(f"https://plc.directory/{did}", timeout=timeout)
        elif did.startswith("did:web:"):
            domain = did.removeprefix("did:web:")
            doc = requests.get(
                f"https://{domain}/.well-known/did.json", timeout=timeout
            )
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
        pds_url = _validate_pds_url(pds)

    return did, pds_url.rstrip("/")


def _raise_for_error(resp):
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
        resp = requests.post(
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
        resp = requests.post(
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
        resp = requests.post(
            f"{self.pds_url}/xrpc/com.atproto.repo.uploadBlob",
            data=data,
            headers={**self._headers(), "Content-Type": mime_type},
            timeout=conf.get_setting("TIMEOUT"),
        )
        _raise_for_error(resp)
        return resp.json()["blob"]


def xrpc_get(base_url, nsid, params=None, timeout=None):
    """Unauthenticated XRPC GET (public reads: listRecords, getPostThread...)."""
    resp = requests.get(
        f"{base_url.rstrip('/')}/xrpc/{nsid}",
        params=params or {},
        timeout=timeout if timeout is not None else conf.get_setting("TIMEOUT"),
    )
    _raise_for_error(resp)
    return resp.json()
