"""The write path: tenants publish standard.site documents from the dashboard.

Documents are written straight into the tenant's own PDS through their OAuth
grant — mosaic stores nothing. Each document gets a TID rkey minted here (so
its permalink `/posts/<rkey>` is known before the write) and a
`site.standard.publication` record (rkey ``self``) is ensured on first
publish so readers/other AppViews can attribute documents to the site.
"""

import html
import logging
import secrets
import time

import markdown as md
import requests
from django.core.cache import cache
from django.utils.html import strip_tags

from django_mosaic.atproto import conf as at_conf
from django_mosaic.atproto import lexicons
from django_mosaic.atproto import preview as preview_mod
from django_mosaic.atproto.client import AtprotoError, xrpc_get

from . import conf

logger = logging.getLogger("django_mosaic.hosted")

TITLE_MAX = 300
DESCRIPTION_MAX = 600
# One guard for both textContent and the inline markdown block; v1 composer
# posts must fit comfortably inside a PDS record.
BODY_MAX_BYTES = 30_000

DOCUMENT_CACHE_SECONDS = 60

# TIDs: 13 chars of base32-sortable encoding a 64-bit int — 53 bits of
# microseconds since the epoch, 10 bits of clock id, top bit zero.
TID_ALPHABET = "234567abcdefghijklmnopqrstuvwxyz"


class ComposerError(Exception):
    """Raised when a publish is invalid or the PDS write fails."""


def generate_tid():
    value = (int(time.time() * 1_000_000) << 10) | secrets.randbelow(1024)
    chars = []
    for _ in range(13):
        chars.append(TID_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


_TID_SET = frozenset(TID_ALPHABET)


def is_valid_tid(rkey):
    """Whether `rkey` is a syntactically valid TID (13 chars of the alphabet).

    Guards the public /posts/<rkey> route: an arbitrary segment would flow
    into a cache key (rejected by memcached for length/chars) and waste a PDS
    round-trip plus a negative-cache entry per unique junk value.
    """
    return (
        isinstance(rkey, str)
        and len(rkey) == 13
        and rkey[0] in "234567abcdefghij"  # top two bits zero
        and all(c in _TID_SET for c in rkey)
    )


def site_url(tenant):
    """The canonical base URL for a tenant's site (custom domain when live)."""
    if tenant.custom_domain and tenant.domain_verified_at:
        return f"https://{tenant.custom_domain}"
    return f"https://{tenant.subdomain}.{conf.base_domain()}"


def _flow():
    from django_mosaic.atproto.oauth import flow

    return flow


def ensure_publication(session, tenant):
    """The at-uri of the tenant's publication record, created if missing.

    Only a definitive "record not found" counts as missing. A transient PDS
    error (5xx, timeout) must NOT be mistaken for absence — creating then
    would clobber an existing publication record (possibly written by another
    standard.site app) with unversioned data loss in the user's own repo.
    """
    uri = f"at://{session.did}/{at_conf.PUBLICATION_NSID}/self"
    try:
        xrpc_get(
            session.pds_url,
            "com.atproto.repo.getRecord",
            {
                "repo": session.did,
                "collection": at_conf.PUBLICATION_NSID,
                "rkey": "self",
            },
        )
        return uri
    except AtprotoError as e:
        if "RecordNotFound" not in str(e) and " 400" not in str(e):
            raise ComposerError(
                "Could not check your publication record; not overwriting it. "
                "Try again in a moment."
            ) from e
    except requests.RequestException as e:
        raise ComposerError("Your PDS was unreachable; try again shortly.") from e

    _flow().xrpc_call(
        session,
        "com.atproto.repo.putRecord",
        method="POST",
        json_body={
            "repo": session.did,
            "collection": at_conf.PUBLICATION_NSID,
            "rkey": "self",
            "record": {
                "$type": at_conf.PUBLICATION_NSID,
                "url": site_url(tenant),
                "name": tenant.handle,
            },
        },
    )
    return uri


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def publish(session, tenant, title, body_markdown, description=""):
    """Write a site.standard.document to the tenant's repo; returns its path.

    Raises ComposerError on validation problems or write failure.
    """
    title = (title or "").strip()[:TITLE_MAX]
    description = (description or "").strip()[:DESCRIPTION_MAX]
    body_markdown = (body_markdown or "").strip()
    if not title:
        raise ComposerError("A title is required.")
    if not body_markdown:
        raise ComposerError("Write something first.")
    if len(body_markdown.encode("utf-8")) > BODY_MAX_BYTES:
        raise ComposerError(
            f"Posts are limited to {BODY_MAX_BYTES // 1000} kB of markdown for now."
        )

    flow = _flow()
    publication_uri = ensure_publication(session, tenant)
    rkey = generate_tid()
    path = f"/posts/{rkey}"
    record = {
        "$type": at_conf.DOCUMENT_NSID,
        "site": publication_uri,
        "path": path,
        "title": title,
        "textContent": html.unescape(strip_tags(md.markdown(body_markdown))).strip(),
        "content": [
            {
                "$type": at_conf.get_setting("CONTENT_NSID"),
                "markdown": body_markdown,
            }
        ],
        "publishedAt": _iso_now(),
    }
    if description:
        record["description"] = description
    try:
        # createRecord (not putRecord) so a TID collision fails loudly instead
        # of silently overwriting an existing document at the same rkey.
        flow.xrpc_call(
            session,
            "com.atproto.repo.createRecord",
            method="POST",
            json_body={
                "repo": session.did,
                "collection": at_conf.DOCUMENT_NSID,
                "rkey": rkey,
                "record": record,
            },
        )
    except flow.OAuthError as e:
        raise ComposerError(str(e)) from e
    except requests.RequestException as e:
        raise ComposerError("Your PDS was unreachable; try again shortly.") from e

    _invalidate_read_caches(session.did)
    logger.info("Published document %s for %s", rkey, session.did)
    return {"rkey": rkey, "path": path, "url": f"{site_url(tenant)}{path}"}


def _invalidate_read_caches(did):
    """Drop the cached reads that would hide a just-published document."""
    cache.delete(f"mosaic_atproto:collections:{did}")
    for limit in (preview_mod.RECORDS_PER_SECTION, lexicons.MAX_RECORDS):
        cache.delete(f"mosaic_atproto:records:{did}:{at_conf.DOCUMENT_NSID}:{limit}")


def get_document(identity, rkey):
    """One site.standard.document record value from a repo, or None."""
    cache_key = f"mosaic_hosted:document:{identity.did}:{rkey}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    try:
        data = xrpc_get(
            identity.pds_url,
            "com.atproto.repo.getRecord",
            {
                "repo": identity.did,
                "collection": at_conf.DOCUMENT_NSID,
                "rkey": rkey,
            },
        )
        value = data.get("value") or None
    except Exception:  # noqa: BLE001 - render path must degrade
        value = None
    cache.set(cache_key, value or "", DOCUMENT_CACHE_SECONDS)
    return value


def document_markdown(value):
    """The markdown source from a document's content union, if present."""
    for block in value.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("markdown"), str):
            return block["markdown"]
    return None
