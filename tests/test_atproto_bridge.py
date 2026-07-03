"""Tests for the ATProto bridge (publisher, signals, well-known views).

All network I/O is mocked at the Session/client boundary.
"""

import io
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from django_mosaic.atproto import conf, publisher
from django_mosaic.atproto.models import DocumentRecord, PublicationRecord
from django_mosaic.models import Author, ContentImage, Namespace, Post


def _make_image(name="cover.png"):
    buf = io.BytesIO()
    Image.new("RGB", (120, 90), (40, 80, 120)).save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/png")


class FakeSession:
    """Records XRPC calls and fabricates PDS responses."""

    def __init__(self):
        self.did = "did:plc:testuser123"
        self.calls = []
        self._counter = 0

    def _result(self, collection):
        self._counter += 1
        rkey = f"rkey{self._counter}"
        return {
            "uri": f"at://{self.did}/{collection}/{rkey}",
            "cid": f"cid{self._counter}",
        }

    def create_record(self, collection, record, rkey=None):
        self.calls.append(("create", collection, record))
        return self._result(collection)

    def put_record(self, collection, rkey, record):
        self.calls.append(("put", collection, record))
        return {"uri": f"at://{self.did}/{collection}/{rkey}", "cid": "cid-updated"}

    def delete_record(self, collection, rkey):
        self.calls.append(("delete", collection, rkey))
        return {}

    def upload_blob(self, data, mime_type):
        self.calls.append(("blob", mime_type, len(data)))
        return {"$type": "blob", "ref": {"$link": "bafyfake"}, "mimeType": mime_type}


class BridgeTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public = Namespace.objects.create(name="public")
        cls.private = Namespace.objects.create(name="private")
        user = User.objects.create_user("atuser")
        cls.author = Author.objects.create(user=user)

    def make_post(self, **kwargs):
        defaults = dict(
            author=self.author,
            title="Hello ATmosphere",
            content="Some **markdown** content.",
            namespace=self.public,
            is_published=True,
        )
        defaults.update(kwargs)
        return Post.objects.create(**defaults)


class PublisherTest(BridgeTestBase):
    def test_publish_creates_publication_companion_and_document(self):
        post = self.make_post()
        session = FakeSession()
        record = publisher.publish_post(post, session=session)

        collections = [c[1] for c in session.calls]
        self.assertIn(conf.PUBLICATION_NSID, collections)
        self.assertIn(conf.BSKY_POST_NSID, collections)
        self.assertIn(conf.DOCUMENT_NSID, collections)

        # Document content assertions
        doc = next(c[2] for c in session.calls if c[1] == conf.DOCUMENT_NSID)
        self.assertEqual(doc["title"], "Hello ATmosphere")
        self.assertIn("markdown", doc["textContent"])
        self.assertNotIn("**", doc["textContent"], "textContent must be plain")
        self.assertTrue(doc["path"].startswith("/public/posts/"))
        self.assertIn("bskyPostRef", doc)
        self.assertTrue(doc["publishedAt"].endswith("Z"))

        # Companion post embeds the canonical URL
        companion = next(c[2] for c in session.calls if c[1] == conf.BSKY_POST_NSID)
        self.assertEqual(
            companion["embed"]["external"]["uri"],
            f"https://blog.example.com{post.get_absolute_url()}",
        )

        # Local tracking rows exist
        self.assertEqual(PublicationRecord.objects.count(), 1)
        self.assertEqual(record.post, post)
        self.assertTrue(record.bsky_post_uri)

        # The document carries a mosaic-native markdown content block that
        # preserves the source (other AppViews fall back to textContent).
        self.assertIn("content", doc)
        block = doc["content"][0]
        self.assertEqual(block["$type"], conf.get_setting("CONTENT_NSID"))
        self.assertIn("**markdown**", block["markdown"])

    def test_large_content_skips_inline_block(self):
        post = self.make_post(content="x" * 40_000)
        session = FakeSession()
        publisher.publish_post(post, session=session)
        doc = next(c[2] for c in session.calls if c[1] == conf.DOCUMENT_NSID)
        # Too large to embed safely; textContent still carries it.
        self.assertNotIn("content", doc)
        self.assertTrue(doc["textContent"])

    def test_cover_image_uploaded_once_and_reused(self):
        post = self.make_post()
        ContentImage.objects.create(post=post, image=_make_image(), alt="cover")
        session = FakeSession()
        publisher.publish_post(post, session=session)

        blob_uploads = [c for c in session.calls if c[0] == "blob"]
        self.assertEqual(len(blob_uploads), 1, "cover blob uploaded exactly once")

        doc = next(c[2] for c in session.calls if c[1] == conf.DOCUMENT_NSID)
        companion = next(c[2] for c in session.calls if c[1] == conf.BSKY_POST_NSID)
        self.assertIn("coverImage", doc)
        self.assertEqual(doc["coverImage"], companion["embed"]["external"]["thumb"])

    def test_republish_updates_same_rkey_and_keeps_companion(self):
        post = self.make_post()
        session = FakeSession()
        first = publisher.publish_post(post, session=session)
        companion_count_before = sum(
            1 for c in session.calls if c[1] == conf.BSKY_POST_NSID
        )

        publisher.publish_post(post, session=session)
        second = DocumentRecord.objects.get(post=post)
        self.assertEqual(first.rkey, second.rkey, "updates must reuse the rkey")
        companion_count_after = sum(
            1 for c in session.calls if c[1] == conf.BSKY_POST_NSID
        )
        self.assertEqual(
            companion_count_before,
            companion_count_after,
            "companion post is created only once",
        )
        # Update path went through putRecord with updatedAt set.
        put_doc = next(
            c[2] for c in session.calls if c[0] == "put" and c[1] == conf.DOCUMENT_NSID
        )
        self.assertIn("updatedAt", put_doc)

    def test_unpublish_deletes_document(self):
        post = self.make_post()
        session = FakeSession()
        publisher.publish_post(post, session=session)
        publisher.unpublish_post(post, session=session)
        self.assertEqual(DocumentRecord.objects.count(), 0)
        deletes = [c for c in session.calls if c[0] == "delete"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0][1], conf.DOCUMENT_NSID)

    def test_private_namespace_not_syncable(self):
        post = self.make_post(namespace=self.private, title="Secret Post")
        self.assertFalse(publisher.syncable(post))

    def test_draft_not_syncable(self):
        post = self.make_post(is_published=False, title="Draft Post")
        self.assertFalse(publisher.syncable(post))


class AutoPublishSignalTest(BridgeTestBase):
    def test_publish_on_save_when_enabled(self):
        with override_settings(
            MOSAIC_ATPROTO={
                **conf.as_dict(),
                "AUTO_PUBLISH": True,
            }
        ):
            with mock.patch("django_mosaic.atproto.publisher.publish_post") as publish:
                with self.captureOnCommitCallbacks(execute=True):
                    self.make_post()
                publish.assert_called_once()

    def test_no_sync_when_auto_publish_off(self):
        with mock.patch("django_mosaic.atproto.publisher.publish_post") as publish:
            with self.captureOnCommitCallbacks(execute=True):
                self.make_post()
            publish.assert_not_called()

    def test_network_failure_does_not_break_save(self):
        with override_settings(
            MOSAIC_ATPROTO={
                **conf.as_dict(),
                "AUTO_PUBLISH": True,
            }
        ):
            with mock.patch(
                "django_mosaic.atproto.publisher.publish_post",
                side_effect=RuntimeError("pds down"),
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    post = self.make_post()
                self.assertTrue(Post.objects.filter(pk=post.pk).exists())


class WellKnownTest(BridgeTestBase):
    def test_atproto_did(self):
        resp = self.client.get("/.well-known/atproto-did")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "did:plc:testuser123")
        self.assertEqual(resp["Content-Type"], "text/plain")

    def test_publication_uri_served_when_record_exists(self):
        resp = self.client.get("/.well-known/site.standard.publication")
        self.assertEqual(resp.status_code, 404)
        PublicationRecord.objects.create(
            uri="at://did:plc:testuser123/site.standard.publication/abc",
            cid="cid1",
            rkey="abc",
        )
        resp = self.client.get("/.well-known/site.standard.publication")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"site.standard.publication/abc", resp.content)


class DocumentLinkTagTest(BridgeTestBase):
    def test_post_page_includes_document_link_when_synced(self):
        post = self.make_post()
        DocumentRecord.objects.create(
            post=post,
            uri="at://did:plc:testuser123/site.standard.document/xyz",
            cid="c",
            rkey="xyz",
        )
        resp = self.client.get(post.get_absolute_url())
        self.assertContains(resp, 'rel="site.standard.document"')
        self.assertContains(resp, "site.standard.document/xyz")

    def test_post_page_omits_link_when_not_synced(self):
        post = self.make_post()
        resp = self.client.get(post.get_absolute_url())
        self.assertNotContains(resp, 'rel="site.standard.document"')
