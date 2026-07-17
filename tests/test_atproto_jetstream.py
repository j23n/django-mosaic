"""Tests for the Jetstream cache-invalidation consumer (handler logic only —
the websocket loop is a thin reconnect wrapper exercised live)."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from django_mosaic.atproto import conf, jetstream

DID = "did:plc:alice"


def _commit(collection, rkey="abc", did=DID, time_us=1000):
    return json.dumps(
        {
            "did": did,
            "time_us": time_us,
            "kind": "commit",
            "commit": {
                "rev": "x",
                "operation": "create",
                "collection": collection,
                "rkey": rkey,
            },
        }
    )


class HandleEventTest(TestCase):
    def setUp(self):
        cache.clear()

    def _prime(self, keys):
        for key in keys:
            cache.set(key, "cached", 300)

    def test_commit_invalidates_collection_caches(self):
        keys = [
            f"mosaic_atproto:collections:{DID}",
            f"mosaic_atproto:records:{DID}:sh.tangled.repo:5",
            f"mosaic_atproto:records:{DID}:sh.tangled.repo:500",
        ]
        self._prime(keys)
        cursor = jetstream.handle_event(_commit("sh.tangled.repo", time_us=42))
        self.assertEqual(cursor, 42)
        for key in keys:
            self.assertIsNone(cache.get(key), key)

    def test_profile_settings_and_document_caches(self):
        keys = {
            "app.bsky.actor.profile": f"mosaic_atproto:profile:{DID}",
            "blog.mosaic.site.settings": f"mosaic_hosted:settings:{DID}",
            conf.DOCUMENT_NSID: f"mosaic_hosted:document:{DID}:abc",
        }
        for collection, key in keys.items():
            self._prime([key])
            jetstream.handle_event(_commit(collection, rkey="abc"))
            self.assertIsNone(cache.get(key), collection)

    def test_identity_event_drops_handle_cache(self):
        cache.set("mosaic_atproto:identity:alice.example", "cached", 3600)
        cursor = jetstream.handle_event(
            json.dumps(
                {
                    "did": DID,
                    "time_us": 9,
                    "kind": "identity",
                    "identity": {"handle": "alice.example"},
                }
            )
        )
        self.assertEqual(cursor, 9)
        self.assertIsNone(cache.get("mosaic_atproto:identity:alice.example"))

    def test_other_dids_caches_untouched(self):
        other = "mosaic_atproto:collections:did:plc:bob"
        self._prime([other])
        jetstream.handle_event(_commit("sh.tangled.repo"))
        self.assertEqual(cache.get(other), "cached")

    def test_garbage_and_non_commit_events_ignored(self):
        self.assertIsNone(jetstream.handle_event("not json"))
        self.assertIsNone(jetstream.handle_event(json.dumps(["list"])))
        self.assertIsNone(jetstream.handle_event(None))
        # Account events return their cursor but touch nothing.
        cursor = jetstream.handle_event(
            json.dumps({"did": DID, "time_us": 7, "kind": "account"})
        )
        self.assertEqual(cursor, 7)
        # Commit without a collection is tolerated.
        cursor = jetstream.handle_event(
            json.dumps({"did": DID, "time_us": 8, "kind": "commit", "commit": {}})
        )
        self.assertEqual(cursor, 8)


class ConsumeAsyncSafetyTest(TestCase):
    """consume() must invalidate caches through sync_to_async.

    A cache whose mutators are @async_unsafe (as DatabaseCache's are) raises
    SynchronousOnlyOperation if touched directly from the event loop; driving
    one event through consume() proves handle_event is marshalled off it.
    """

    def test_event_invalidates_via_async_wrapper(self):
        import asyncio

        from django.utils.asyncio import async_unsafe

        collection_key = f"mosaic_atproto:collections:{DID}"

        class AsyncUnsafeCache:
            def __init__(self):
                self.deleted = []

            def get(self, key):
                return None

            def set(self, *args, **kwargs):
                pass

            @async_unsafe
            def delete(self, key):
                self.deleted.append(key)

            @async_unsafe
            def delete_many(self, keys):
                self.deleted.extend(keys)

        fake_cache = AsyncUnsafeCache()

        async def _one_then_stop():
            yield _commit("sh.tangled.repo", time_us=55)
            raise asyncio.CancelledError  # break the otherwise-infinite loop

        class FakeConn:
            async def __aenter__(self):
                return _one_then_stop()

            async def __aexit__(self, *args):
                return False

        with (
            mock.patch("websockets.connect", lambda url: FakeConn()),
            mock.patch(
                "django_mosaic.atproto.jetstream.wanted_dids", return_value=[DID]
            ),
            mock.patch("django_mosaic.atproto.jetstream.cache", fake_cache),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(jetstream.consume(url="wss://x", reconnect_delay_max=1))

        # The commit's collection cache was invalidated — and no
        # SynchronousOnlyOperation was raised getting there.
        self.assertIn(collection_key, fake_cache.deleted)


class WantedDidsTest(TestCase):
    def test_owner_plus_active_tenants_deduped(self):
        from django_mosaic.atproto.identity import Identity
        from django_mosaic.hosted.models import Tenant

        Tenant.objects.create(did=DID, handle="alice.example", subdomain="alice")
        Tenant.objects.create(
            did="did:plc:sus",
            handle="sus.example",
            subdomain="sus",
            status=Tenant.STATUS_SUSPENDED,
        )
        owner = Identity("blog.example.com", DID, "https://pds.example.com")
        with mock.patch("django_mosaic.atproto.identity.owner", return_value=owner):
            dids = jetstream.wanted_dids()
        self.assertEqual(dids, [DID])  # owner == tenant deduped; suspended excluded

    def test_no_owner_no_tenants(self):
        with mock.patch(
            "django_mosaic.atproto.identity.owner", side_effect=Exception("boom")
        ):
            self.assertEqual(jetstream.wanted_dids(), [])


class BuildUrlTest(TestCase):
    @override_settings(
        MOSAIC_ATPROTO={**conf.as_dict(), "JETSTREAM_URL": "wss://js.example/subscribe"}
    )
    def test_url_carries_dids_and_cursor(self):
        url = jetstream.build_url([DID, "did:plc:bob"], cursor=123)
        self.assertTrue(url.startswith("wss://js.example/subscribe?"))
        self.assertIn("wantedDids=did%3Aplc%3Aalice", url)
        self.assertIn("wantedDids=did%3Aplc%3Abob", url)
        self.assertIn("cursor=123", url)

    def test_empty_dids_refused(self):
        # An empty wantedDids set would subscribe to the whole firehose.
        with self.assertRaises(ValueError):
            jetstream.build_url([])
