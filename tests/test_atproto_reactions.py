"""Tests for the ATmosphere reactions display (thread + Constellation)."""

from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from django_mosaic.atproto import conf, reactions
from django_mosaic.atproto.models import DocumentRecord
from django_mosaic.models import Author, Namespace, Post

THREAD_RESPONSE = {
    "thread": {
        "post": {
            "uri": "at://did:plc:me/app.bsky.feed.post/comp1",
            "likeCount": 7,
            "repostCount": 2,
            "replyCount": 1,
        },
        "replies": [
            {
                "post": {
                    "uri": "at://did:plc:alice/app.bsky.feed.post/r1",
                    "author": {
                        "handle": "alice.example.com",
                        "displayName": "Alice",
                        "avatar": "https://cdn.example/avatar.jpg",
                    },
                    "record": {
                        "text": "Great post! <script>alert(1)</script>",
                        "createdAt": "2026-07-01T12:00:00.000Z",
                    },
                },
                "replies": [
                    {
                        "post": {
                            "uri": "at://did:plc:bob/app.bsky.feed.post/r2",
                            "author": {"handle": "bob.test"},
                            "record": {
                                "text": "Nested reply",
                                "createdAt": "2026-07-01T13:00:00.000Z",
                            },
                        }
                    }
                ],
            }
        ],
    }
}

CONSTELLATION_RESPONSE = {
    "links": {
        "app.bsky.feed.like": {".subject.uri": 7},
        "site.standard.graph.recommend": {".document": 3},
        "pub.leaflet.comment": {".subject": 2},
    }
}


class ReactionsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
        user = User.objects.create_user("ruser")
        cls.author = Author.objects.create(user=user)
        cls.post = Post.objects.create(
            author=cls.author,
            title="Reacted Post",
            slug="reacted-post",
            content="body",
            namespace=cls.ns,
            is_published=True,
        )
        cls.document = DocumentRecord.objects.create(
            post=cls.post,
            uri="at://did:plc:me/site.standard.document/doc1",
            cid="c1",
            rkey="doc1",
            bsky_post_uri="at://did:plc:me/app.bsky.feed.post/comp1",
            bsky_post_cid="c2",
        )

    def setUp(self):
        cache.clear()


class FetchThreadTest(ReactionsTestBase):
    def test_thread_counts_and_flattened_replies(self):
        with mock.patch(
            "django_mosaic.atproto.reactions.xrpc_get",
            return_value=THREAD_RESPONSE,
        ):
            thread = reactions.fetch_thread(self.document.bsky_post_uri)
        self.assertEqual(thread["like_count"], 7)
        self.assertEqual(thread["repost_count"], 2)
        self.assertEqual(len(thread["replies"]), 2)
        self.assertEqual(thread["replies"][0]["display_name"], "Alice")
        self.assertEqual(thread["replies"][1]["depth"], 1)
        self.assertEqual(
            thread["web_url"], "https://bsky.app/profile/did:plc:me/post/comp1"
        )

    def test_thread_failure_returns_none(self):
        with mock.patch(
            "django_mosaic.atproto.reactions.xrpc_get",
            side_effect=RuntimeError("appview down"),
        ):
            self.assertIsNone(reactions.fetch_thread("at://x/app.bsky.feed.post/y"))

    def test_thread_is_cached(self):
        with mock.patch(
            "django_mosaic.atproto.reactions.xrpc_get",
            return_value=THREAD_RESPONSE,
        ) as fetch:
            reactions.fetch_thread(self.document.bsky_post_uri)
            reactions.fetch_thread(self.document.bsky_post_uri)
        fetch.assert_called_once()

    def test_render_path_uses_short_timeout(self):
        with mock.patch(
            "django_mosaic.atproto.reactions.xrpc_get",
            return_value=THREAD_RESPONSE,
        ) as fetch:
            reactions.fetch_thread(self.document.bsky_post_uri)
        # The render-path fetch must pass the short REACTIONS_TIMEOUT, not the
        # long publish TIMEOUT, so a slow AppView can't hang a post page.
        self.assertEqual(
            fetch.call_args.kwargs["timeout"], conf.get_setting("REACTIONS_TIMEOUT")
        )

    def test_non_blocking_returns_cache_only(self):
        # No cache + non-blocking => no live call, returns None.
        with mock.patch(
            "django_mosaic.atproto.reactions.xrpc_get",
            side_effect=AssertionError("must not call the network"),
        ):
            self.assertIsNone(
                reactions.fetch_thread(self.document.bsky_post_uri, blocking=False)
            )


class ConstellationTest(ReactionsTestBase):
    def _mock_requests_get(self, payload):
        resp = mock.Mock()
        resp.json.return_value = payload
        resp.raise_for_status.return_value = None
        return mock.patch(
            "django_mosaic.atproto.reactions.requests.get", return_value=resp
        )

    def test_counts_parsed_labeled_and_deduplicated(self):
        with self._mock_requests_get(CONSTELLATION_RESPONSE):
            counts = reactions.fetch_crossapp_counts([self.document.uri])
        by_collection = {c["collection"]: c for c in counts}
        # bsky likes are dropped (already shown via the AppView thread)
        self.assertNotIn("app.bsky.feed.like", by_collection)
        self.assertEqual(by_collection["site.standard.graph.recommend"]["count"], 3)
        self.assertEqual(
            by_collection["site.standard.graph.recommend"]["label"], "recommends"
        )
        self.assertEqual(by_collection["pub.leaflet.comment"]["count"], 2)

    def test_unwrapped_and_list_shapes_tolerated(self):
        payload = {"sh.tangled.feed.star": {".subject": ["a", "b"]}}
        with self._mock_requests_get(payload):
            counts = reactions.fetch_crossapp_counts(["at://x/y/z"])
        self.assertEqual(counts[0]["collection"], "sh.tangled.feed.star")
        self.assertEqual(counts[0]["count"], 2)

    def test_constellation_failure_degrades_to_empty(self):
        with mock.patch(
            "django_mosaic.atproto.reactions.requests.get",
            side_effect=RuntimeError("index down"),
        ):
            counts = reactions.fetch_crossapp_counts([self.document.uri])
        self.assertEqual(counts, [])


class ReactionsOnPostPageTest(ReactionsTestBase):
    def _render(self):
        return self.client.get(self.post.get_absolute_url())

    def test_reactions_section_rendered_with_comments(self):
        # Constellation is queried once per target (AT-URI + canonical URL);
        # only the AT-URI has backlinks in this scenario.
        with_links = mock.Mock()
        with_links.json.return_value = CONSTELLATION_RESPONSE
        with_links.raise_for_status.return_value = None
        empty = mock.Mock()
        empty.json.return_value = {"links": {}}
        empty.raise_for_status.return_value = None
        with (
            mock.patch(
                "django_mosaic.atproto.reactions.xrpc_get",
                return_value=THREAD_RESPONSE,
            ),
            mock.patch(
                "django_mosaic.atproto.reactions.requests.get",
                side_effect=[with_links, empty],
            ),
        ):
            resp = self._render()
        self.assertContains(resp, "atproto-reactions")
        self.assertContains(resp, "♥ 7")
        self.assertContains(resp, "Reply on Bluesky")
        self.assertContains(resp, "Alice")
        self.assertContains(resp, "3 recommends")
        # Comment text must be escaped, never raw HTML.
        self.assertNotContains(resp, "<script>alert(1)</script>", html=False)
        self.assertContains(resp, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_section_absent_for_unsynced_post(self):
        unsynced = Post.objects.create(
            author=self.author,
            title="Unsynced",
            slug="unsynced",
            content="body",
            namespace=self.ns,
            is_published=True,
        )
        resp = self.client.get(unsynced.get_absolute_url())
        self.assertNotContains(resp, "atproto-reactions")

    def test_page_survives_all_sources_down(self):
        with (
            mock.patch(
                "django_mosaic.atproto.reactions.xrpc_get",
                side_effect=RuntimeError("down"),
            ),
            mock.patch(
                "django_mosaic.atproto.reactions.requests.get",
                side_effect=RuntimeError("down"),
            ),
        ):
            resp = self._render()
        self.assertEqual(resp.status_code, 200)
