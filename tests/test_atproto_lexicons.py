"""Tests for lexicon collection pages (Tangled repos, BookHive books)."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from django_mosaic.atproto import conf, lexicons

TANGLED_RECORDS = {
    "records": [
        {
            "uri": "at://did:plc:testuser123/sh.tangled.repo/3aaa",
            "cid": "c1",
            "value": {
                "$type": "sh.tangled.repo",
                "name": "django-mosaic",
                "description": "A simple blog engine",
                "knot": "knot1.tangled.org",
            },
        },
        {
            "uri": "at://did:plc:testuser123/sh.tangled.repo/3bbb",
            "cid": "c2",
            "value": {"$type": "sh.tangled.repo", "name": "dotfiles"},
        },
    ]
}

BOOK_RECORDS = {
    "records": [
        {
            "uri": "at://did:plc:testuser123/buzz.bookhive.book/3ccc",
            "cid": "c3",
            "value": {
                "$type": "buzz.bookhive.book",
                "title": "The Dispossessed",
                "authors": "Ursula K. Le Guin",
                "status": "buzz.bookhive.defs#finished",
                "stars": 9,
                "review": "A classic.",
                "hiveId": "h1",
                "createdAt": "2026-01-01T00:00:00Z",
                "cover": {
                    "$type": "blob",
                    "ref": {"$link": "bafycover"},
                    "mimeType": "image/jpeg",
                },
            },
        }
    ]
}


class LexiconTestBase(TestCase):
    def setUp(self):
        cache.clear()


class ListRecordsTest(LexiconTestBase):
    def test_records_fetched_and_shaped(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            return_value=TANGLED_RECORDS,
        ):
            records = lexicons.list_records("sh.tangled.repo")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["value"]["name"], "django-mosaic")
        self.assertEqual(records[0]["rkey"], "3aaa")

    def test_failure_degrades_to_empty(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            side_effect=RuntimeError("pds down"),
        ):
            self.assertEqual(lexicons.list_records("sh.tangled.repo"), [])

    def test_results_cached(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            return_value=TANGLED_RECORDS,
        ) as fetch:
            lexicons.list_records("sh.tangled.repo")
            lexicons.list_records("sh.tangled.repo")
        fetch.assert_called_once()

    def test_read_path_uses_short_timeout(self):
        # Reads target an arbitrary (possibly attacker-chosen) PDS in preview
        # mode, so they must use the short READ_TIMEOUT, not the 15s publish
        # TIMEOUT, or a stalling PDS could tie up a worker per page section.
        from django_mosaic.atproto import conf as atconf

        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            return_value=TANGLED_RECORDS,
        ) as fetch:
            lexicons.list_records("sh.tangled.repo")
        self.assertEqual(
            fetch.call_args.kwargs["timeout"], atconf.get_setting("READ_TIMEOUT")
        )
        self.assertLess(
            atconf.get_setting("READ_TIMEOUT"), atconf.get_setting("TIMEOUT")
        )

    def test_blob_url(self):
        url = lexicons.blob_url(
            {"$type": "blob", "ref": {"$link": "bafyx"}, "mimeType": "image/jpeg"}
        )
        self.assertEqual(
            url,
            "https://pds.example.com/xrpc/com.atproto.sync.getBlob"
            "?did=did:plc:testuser123&cid=bafyx",
        )


class ProjectsPageTest(LexiconTestBase):
    def test_projects_page_renders_tangled_repos(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            return_value=TANGLED_RECORDS,
        ):
            resp = self.client.get("/projects")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "django-mosaic")
        self.assertContains(resp, "A simple blog engine")
        self.assertContains(resp, "https://tangled.org/@blog.example.com/django-mosaic")

    def test_empty_collection_renders_placeholder(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            return_value={"records": []},
        ):
            resp = self.client.get("/projects")
        self.assertContains(resp, "Nothing here yet")


class BooksPageTest(LexiconTestBase):
    def test_books_page_renders_bookhive_records(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.xrpc_get",
            return_value=BOOK_RECORDS,
        ):
            resp = self.client.get("/books")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "The Dispossessed")
        self.assertContains(resp, "Ursula K. Le Guin")
        self.assertContains(resp, "finished")
        self.assertContains(resp, "4.5★")
        self.assertContains(resp, "A classic.")
        self.assertContains(resp, "cid=bafycover")


class GenericFallbackTest(LexiconTestBase):
    def test_unknown_collection_uses_generic_template(self):
        # A page configured for a collection we ship no template for.
        pages = {
            "scrobbles": {
                "collection": "fm.teal.alpha.feed.play",
                "title": "Music",
            }
        }
        payload = {
            "records": [
                {
                    "uri": "at://did:plc:testuser123/fm.teal.alpha.feed.play/3ddd",
                    "cid": "c9",
                    "value": {
                        "$type": "fm.teal.alpha.feed.play",
                        "trackName": "Paranoid Android",
                        "artists": [{"artistName": "Radiohead"}],
                    },
                }
            ]
        }
        with override_settings(
            MOSAIC_ATPROTO={**conf.as_dict(), "LEXICON_PAGES": pages}
        ):
            with mock.patch(
                "django_mosaic.atproto.lexicons.xrpc_get",
                return_value=payload,
            ):
                # Note: URL routes are built at import time from settings, so
                # the default slugs stay mounted; render the view directly.
                from django.test import RequestFactory

                from django_mosaic.atproto.views import lexicon_page

                request = RequestFactory().get("/scrobbles")
                resp = lexicon_page(request, page="scrobbles")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Paranoid Android", resp.content.decode())

    def test_unknown_page_404s(self):
        resp = self.client.get("/definitely-not-configured")
        # Falls through to mosaic's namespace catch-all, which 404s for an
        # unknown namespace.
        self.assertEqual(resp.status_code, 404)
