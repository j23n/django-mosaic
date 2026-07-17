"""Identity as an explicit value instead of a settings singleton.

Every read path (lexicon pages, blob URLs, preview mode) operates on an
``Identity`` — the site owner's by default, or any handle's in preview mode.
This is the M0 groundwork for multi-tenant hosting: request handlers decide
whose repo is being rendered; nothing below them reads global settings.
"""

import hashlib
import logging
import re
from dataclasses import dataclass

from django.core.cache import cache

from . import conf
from .client import resolve_identity, resolve_pds

logger = logging.getLogger("django_mosaic.atproto")

IDENTITY_CACHE_SECONDS = 3600

# A handle arrives from the /@<handle> URL, so it can contain spaces or control
# characters that some cache backends (memcached) reject as keys. Use it
# verbatim only when it is a safe, bounded token; otherwise hash it.
_SAFE_HANDLE_KEY = re.compile(r"^[a-z0-9.\-]{1,200}$")


def _identity_cache_key(handle):
    normalized = handle.lower()
    if _SAFE_HANDLE_KEY.match(normalized):
        return f"mosaic_atproto:identity:{normalized}"
    digest = hashlib.sha256(handle.encode("utf-8")).hexdigest()[:32]
    return f"mosaic_atproto:identity:h:{digest}"


@dataclass(frozen=True)
class Identity:
    handle: str
    did: str
    pds_url: str


def resolve(handle):
    """Resolve any handle to an Identity, cached per handle.

    Raises AtprotoError on failure (callers on the render path catch it).
    """
    cache_key = _identity_cache_key(handle)
    cached = cache.get(cache_key)
    if cached:
        return cached
    did, pds_url = resolve_identity(handle)
    identity = Identity(handle=handle, did=did, pds_url=pds_url)
    cache.set(cache_key, identity, IDENTITY_CACHE_SECONDS)
    return identity


def resolve_did(did, handle=""):
    """Resolve an Identity from an immutable DID (not the mutable handle).

    Use this when the DID is the trusted identifier — e.g. a hosted tenant
    whose ownership was proven at claim time — so a later handle takeover
    can't redirect the render to a different account. ``handle`` is carried
    through for display only. Cached per DID.

    Raises AtprotoError on failure (render-path callers catch it).
    """
    cache_key = f"mosaic_atproto:identity_did:{did}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    pds_url = resolve_pds(did)
    identity = Identity(handle=handle, did=did, pds_url=pds_url)
    cache.set(cache_key, identity, IDENTITY_CACHE_SECONDS)
    return identity


def owner():
    """The site owner's Identity, or None when no handle is configured."""
    handle = conf.get_setting("HANDLE")
    if not handle:
        return None
    return resolve(handle)
