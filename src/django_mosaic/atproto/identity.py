"""Identity as an explicit value instead of a settings singleton.

Every read path (lexicon pages, blob URLs, preview mode) operates on an
``Identity`` — the site owner's by default, or any handle's in preview mode.
This is the M0 groundwork for multi-tenant hosting: request handlers decide
whose repo is being rendered; nothing below them reads global settings.
"""

import logging
from dataclasses import dataclass

from django.core.cache import cache

from . import conf
from .client import resolve_identity

logger = logging.getLogger("django_mosaic.atproto")

IDENTITY_CACHE_SECONDS = 3600


@dataclass(frozen=True)
class Identity:
    handle: str
    did: str
    pds_url: str


def resolve(handle):
    """Resolve any handle to an Identity, cached per handle.

    Raises AtprotoError on failure (callers on the render path catch it).
    """
    cache_key = f"mosaic_atproto:identity:{handle}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    did, pds_url = resolve_identity(handle)
    identity = Identity(handle=handle, did=did, pds_url=pds_url)
    cache.set(cache_key, identity, IDENTITY_CACHE_SECONDS)
    return identity


def owner():
    """The site owner's Identity, or None when no handle is configured."""
    handle = conf.get_setting("HANDLE")
    if not handle:
        return None
    return resolve(handle)
