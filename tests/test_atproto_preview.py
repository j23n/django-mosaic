"""Tests for identity de-singletonization and the /@handle preview mode."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from django_mosaic.atproto import conf, lexicons
from django_mosaic.atproto.client import AtprotoError, _validate_pds_url
from django_mosaic.atproto.identity import Identity

OTHER = Identity(
    handle="other.example.com",
    did="did:plc:other456",
    pds_url="https://pds.other.example",
)

DESCRIBE_RESPONSE = {
    "collections": [
        "app.bsky.feed.post",
        "app.bsky.feed.like",
        "sh.tangled.repo",
        "buzz.bookhive.book",
        "xyz.statusphere.status",
    ]
}


def fake_xrpc_get(base_url, nsid, params=None, timeout=None):
    if nsid == "com.atproto.repo.describeRepo":
        return DESCRIBE_RESPONSE
    if nsid == "com.atproto.repo.listRecords":
        collection = params["collection"]
        if collection == "sh.tangled.repo":
            return {
                "records": [
                    {
                        "uri": f"at://{OTHER.did}/sh.tangled.repo/3aaa",
                        "cid": "c1",
                        "value": {"$type": collection, "name": "other-repo"},
                    }
                ]
            }
        if collection == "buzz.bookhive.book":
            return {
                "records": [
                    {
                        "uri": f"at://{OTHER.did}/buzz.bookhive.book/3bbb",
                        "cid": "c2",
                        "value": {
                            "$type": collection,
                            "title": "Some Book",
                            "authors": "Somebody",
                            "cover": {
                                "$type": "blob",
                                "ref": {"$link": "bafyothercover"},
                                "mimeType": "image/jpeg",
                            },
                        },
                    }
                ]
            }
        return {"records": []}
    if nsid == "app.bsky.actor.getProfile":
        return {
            "displayName": "Other Person",
            "description": "Elsewhere in the ATmosphere",
            "avatar": "https://cdn.example/other-avatar.jpg",
        }
    raise AssertionError(f"unexpected XRPC call: {nsid}")


PREVIEW_ON = {**conf.as_dict(), "PREVIEW": True}


class PreviewViewTest(TestCase):
    def setUp(self):
        cache.clear()

    def _render(self):
        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve_identity",
                return_value=(OTHER.did, OTHER.pds_url),
            ),
            mock.patch(
                "django_mosaic.atproto.lexicons.xrpc_get",
                side_effect=fake_xrpc_get,
            ),
            mock.patch(
                "django_mosaic.atproto.preview.xrpc_get",
                side_effect=fake_xrpc_get,
            ),
        ):
            return self.client.get("/@other.example.com")

    @override_settings(MOSAIC_ATPROTO=PREVIEW_ON)
    def test_preview_renders_profile_and_sections(self):
        resp = self._render()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Other Person")
        self.assertContains(resp, "@other.example.com")
        self.assertContains(resp, "Projects")
        self.assertContains(resp, "other-repo")
        self.assertContains(resp, "Books")
        self.assertContains(resp, "Some Book")
        # Unknown non-bsky collections are listed by name.
        self.assertContains(resp, "xyz.statusphere.status")
        # Bluesky-internal collections are not.
        self.assertNotContains(resp, "app.bsky.feed.like")

    @override_settings(MOSAIC_ATPROTO=PREVIEW_ON)
    def test_preview_links_and_blobs_use_previewed_identity(self):
        resp = self._render()
        # Tangled link built from the previewed handle, not the owner's.
        self.assertContains(resp, "https://tangled.org/@other.example.com/other-repo")
        self.assertNotContains(resp, "tangled.org/@blog.example.com")
        # Blob URL built from the previewed DID/PDS, not the owner's.
        self.assertContains(resp, "pds.other.example")
        self.assertContains(resp, f"did={OTHER.did}")

    def test_preview_disabled_404s(self):
        # Default settings: PREVIEW is False.
        resp = self.client.get("/@other.example.com")
        self.assertEqual(resp.status_code, 404)

    @override_settings(MOSAIC_ATPROTO=PREVIEW_ON)
    def test_unresolvable_handle_404s(self):
        with mock.patch(
            "django_mosaic.atproto.identity.resolve_identity",
            side_effect=AtprotoError("no such handle"),
        ):
            resp = self.client.get("/@nobody.invalid")
        self.assertEqual(resp.status_code, 404)


class IdentityScopingTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_owner_overrides_do_not_leak_to_other_handles(self):
        """conf DID/PDS_URL must only shortcut resolution of the owner."""
        from django_mosaic.atproto.client import resolve_identity

        resolve_response = mock.Mock()
        resolve_response.status_code = 200
        resolve_response.json.return_value = {"did": "did:plc:networkresolved"}
        doc_response = mock.Mock()
        doc_response.status_code = 200
        doc_response.json.return_value = {
            "service": [
                {
                    "type": "AtprotoPersonalDataServer",
                    "serviceEndpoint": "https://pds.network.example",
                }
            ]
        }
        with mock.patch(
            "django_mosaic.atproto.client.requests.get",
            side_effect=[resolve_response, doc_response],
        ):
            did, pds = resolve_identity("someone-else.example")
        self.assertEqual(did, "did:plc:networkresolved")
        self.assertEqual(pds, "https://pds.network.example")

    def test_owner_resolution_uses_overrides_without_network(self):
        from django_mosaic.atproto.client import resolve_identity

        with mock.patch(
            "django_mosaic.atproto.client.requests.get",
            side_effect=AssertionError("must not hit the network"),
        ):
            did, pds = resolve_identity("blog.example.com")
        self.assertEqual(did, "did:plc:testuser123")
        self.assertEqual(pds, "https://pds.example.com")

    def test_records_cached_per_identity(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            side_effect=fake_xrpc_get,
        ) as fetch:
            lexicons.list_records("sh.tangled.repo", identity=OTHER)
            lexicons.list_records("sh.tangled.repo", identity=OTHER)
            other_b = Identity(
                handle="b.example", did="did:plc:bbb", pds_url="https://pds.b"
            )
            lexicons.list_records("sh.tangled.repo", identity=other_b)
        # Same identity hits cache; a different identity fetches again.
        self.assertEqual(fetch.call_count, 2)


class PdsUrlValidationTest(TestCase):
    def test_https_public_hostname_accepted(self):
        self.assertEqual(
            _validate_pds_url("https://pds.example.com"),
            "https://pds.example.com",
        )

    def test_http_rejected(self):
        with self.assertRaises(AtprotoError):
            _validate_pds_url("http://pds.example.com")

    def test_ip_literal_rejected(self):
        with self.assertRaises(AtprotoError):
            _validate_pds_url("https://169.254.169.254")

    def test_localhost_and_internal_rejected(self):
        for url in (
            "https://localhost",
            "https://foo.local",
            "https://metadata.internal",
        ):
            with self.assertRaises(AtprotoError):
                _validate_pds_url(url)
