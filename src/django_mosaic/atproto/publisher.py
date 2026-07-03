"""Publishes mosaic posts to the PDS as site.standard.document records.

Flow per post (create): ensure publication record exists -> optionally create
a companion app.bsky.feed.post (external embed of the canonical URL, thumb
from the featured image) -> create the document with bskyPostRef. Updates
put the same rkey again; the companion post is created once and kept.
"""

import html
import logging

import markdown as md
from django.utils import timezone
from django.utils.html import strip_tags

from . import conf
from .client import Session
from .models import DocumentRecord, PublicationRecord

logger = logging.getLogger("django_mosaic.atproto")

# app.bsky.feed.post text is capped at 300 graphemes.
COMPANION_TEXT_MAX = 300
# standard.site blobs (coverImage / embed thumb) are capped at 1 MB.
BLOB_MAX_BYTES = 1_000_000


def text_content(post):
    """Plain-text rendition of the post for the document's textContent."""
    return html.unescape(strip_tags(md.markdown(post.published_content))).strip()


def canonical_url(post):
    return f"{conf.publication_url()}{post.get_absolute_url()}"


def _iso(dt):
    return (
        dt.astimezone(timezone.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )


def ensure_publication(session):
    """Create or update the site.standard.publication record; return it."""
    pub_conf = conf.get_setting("PUBLICATION")
    record = {
        "$type": conf.PUBLICATION_NSID,
        "url": conf.publication_url(),
        "name": pub_conf.get("NAME", ""),
    }
    if pub_conf.get("DESCRIPTION"):
        record["description"] = pub_conf["DESCRIPTION"]

    tracked = PublicationRecord.objects.first()
    if tracked:
        result = session.put_record(conf.PUBLICATION_NSID, tracked.rkey, record)
        tracked.uri, tracked.cid = result["uri"], result["cid"]
        tracked.save(update_fields=["uri", "cid", "updated_at"])
        return tracked

    result = session.create_record(conf.PUBLICATION_NSID, record)
    rkey = result["uri"].rsplit("/", 1)[-1]
    return PublicationRecord.objects.create(
        uri=result["uri"], cid=result["cid"], rkey=rkey
    )


def _upload_cover(session, post):
    """Upload the post's featured thumbnail as a blob, once, for reuse as both
    the document coverImage and the companion post's embed thumb. The 600px
    thumb is safely under the 1 MB standard.site blob limit."""
    image = post.featured_image
    if not image or not image.thumb:
        return None
    try:
        image.thumb.open("rb")
        data = image.thumb.read()
        image.thumb.close()
    except OSError as e:
        logger.warning(f"Could not read thumb for post {post.pk}: {e}")
        return None
    if len(data) > BLOB_MAX_BYTES:
        return None
    return session.upload_blob(data, "image/jpeg")


def _content_block(post):
    """Mosaic-native markdown entry for the document's open `content` union.

    Returns None when the markdown is too large to embed inline safely."""
    markdown_source = post.published_content or ""
    if len(markdown_source.encode("utf-8")) > conf.get_setting(
        "CONTENT_MAX_INLINE_BYTES"
    ):
        return None
    return {
        "$type": conf.get_setting("CONTENT_NSID"),
        "markdown": markdown_source,
    }


def _create_companion_post(session, post, thumb_blob):
    url = canonical_url(post)
    text = conf.get_setting("COMPANION_TEXT").format(title=post.title, url=url)
    if len(text) > COMPANION_TEXT_MAX:
        text = text[: COMPANION_TEXT_MAX - 1] + "…"
    external = {
        "uri": url,
        "title": post.title,
        "description": post.summary,
    }
    if thumb_blob:
        external["thumb"] = thumb_blob
    record = {
        "$type": conf.BSKY_POST_NSID,
        "text": text,
        "createdAt": _iso(timezone.now()),
        "embed": {"$type": "app.bsky.embed.external", "external": external},
    }
    return session.create_record(conf.BSKY_POST_NSID, record)


def build_document(post, publication_uri, bsky_post_ref=None, cover_blob=None):
    record = {
        "$type": conf.DOCUMENT_NSID,
        "site": publication_uri,
        "path": post.get_absolute_url(),
        "title": post.title,
        "description": post.summary,
        "textContent": text_content(post),
        "tags": [t.name for t in post.tags.all()],
        "publishedAt": _iso(post.published_at or timezone.now()),
    }
    content_block = _content_block(post)
    if content_block:
        record["content"] = [content_block]
    if cover_blob:
        record["coverImage"] = cover_blob
    tracked = DocumentRecord.objects.filter(post=post).first()
    if tracked:
        record["updatedAt"] = _iso(timezone.now())
    if bsky_post_ref:
        record["bskyPostRef"] = bsky_post_ref
    return record


def publish_post(post, session=None):
    """Publish (or update) one post to the PDS. Returns the DocumentRecord."""
    if not conf.enabled():
        raise RuntimeError("MOSAIC_ATPROTO is not configured.")
    session = session or Session.create()

    publication = ensure_publication(session)
    tracked = DocumentRecord.objects.filter(post=post).first()

    # Upload the featured thumbnail once; reuse for coverImage + companion embed.
    cover_blob = _upload_cover(session, post)

    bsky_post_ref = None
    if tracked and tracked.bsky_post_uri:
        bsky_post_ref = {"uri": tracked.bsky_post_uri, "cid": tracked.bsky_post_cid}
    elif conf.get_setting("COMPANION_POST"):
        companion = _create_companion_post(session, post, cover_blob)
        bsky_post_ref = {"uri": companion["uri"], "cid": companion["cid"]}

    record = build_document(post, publication.uri, bsky_post_ref, cover_blob)

    if tracked:
        result = session.put_record(conf.DOCUMENT_NSID, tracked.rkey, record)
        tracked.uri, tracked.cid = result["uri"], result["cid"]
        if bsky_post_ref:
            tracked.bsky_post_uri = bsky_post_ref["uri"]
            tracked.bsky_post_cid = bsky_post_ref["cid"]
        tracked.save()
        logger.info(f"Updated {tracked.uri} for post {post.pk}")
        return tracked

    result = session.create_record(conf.DOCUMENT_NSID, record)
    tracked = DocumentRecord.objects.create(
        post=post,
        uri=result["uri"],
        cid=result["cid"],
        rkey=result["uri"].rsplit("/", 1)[-1],
        bsky_post_uri=bsky_post_ref["uri"] if bsky_post_ref else "",
        bsky_post_cid=bsky_post_ref["cid"] if bsky_post_ref else "",
    )
    logger.info(f"Published {tracked.uri} for post {post.pk}")
    return tracked


def unpublish_post(post, session=None, delete_companion=False):
    """Delete the document record (and optionally the companion post)."""
    tracked = DocumentRecord.objects.filter(post=post).first()
    if not tracked:
        return
    session = session or Session.create()
    session.delete_record(conf.DOCUMENT_NSID, tracked.rkey)
    if delete_companion and tracked.bsky_post_uri:
        rkey = tracked.bsky_post_uri.rsplit("/", 1)[-1]
        session.delete_record(conf.BSKY_POST_NSID, rkey)
    logger.info(f"Deleted {tracked.uri} for post {post.pk}")
    tracked.delete()


def syncable(post):
    """Should this post exist on the PDS at all?"""
    return (
        conf.enabled()
        and post.is_published
        and post.namespace.name in conf.get_setting("NAMESPACES")
    )
