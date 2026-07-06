"""Tests for M4: the composer write path, document pages, custom CSS."""

from unittest import mock

import pytest

pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from django.core.cache import cache  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.urls import include, path  # noqa: E402

from django_mosaic.atproto.client import AtprotoError  # noqa: E402
from django_mosaic.atproto.identity import Identity  # noqa: E402
from django_mosaic.atproto.models import OAuthSession  # noqa: E402
from django_mosaic.atproto.oauth import flow  # noqa: E402
from django_mosaic.atproto.oauth import views as oauth_views  # noqa: E402
from django_mosaic.hosted import composer, site_settings  # noqa: E402
from django_mosaic.hosted.models import Tenant  # noqa: E402

HOSTED_ON = {"BASE_DOMAIN": "mosaic.example"}
ALICE = Identity("alice.example", "did:plc:alice", "https://pds.alice.example")

urlpatterns = [
    path("", include("django_mosaic.hosted.urls")),
    path("oauth/login", oauth_views.login, name="atproto-oauth-login"),
]

TENANT_MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django_mosaic.hosted.middleware.TenantMiddleware",
]


def _tenant(**overrides):
    fields = {
        "did": "did:plc:alice",
        "handle": "alice.example",
        "subdomain": "alice",
    }
    fields.update(overrides)
    return Tenant.objects.create(**fields)


def _oauth_session():
    return OAuthSession.objects.create(
        did="did:plc:alice",
        handle="alice.example",
        pds_url="https://pds.alice.example",
        auth_server="https://auth.example",
        token_endpoint="https://auth.example/token",
        access_token="at-1",
        dpop_jwk={"kty": "EC"},
    )


def _sign_in(client):
    oauth = _oauth_session()
    session = client.session
    session[flow.DID_KEY] = oauth.did
    session.save()
    return oauth


class TidTest(TestCase):
    def test_tid_shape_and_ordering(self):
        with mock.patch(
            "django_mosaic.hosted.composer.time.time",
            side_effect=[1_751_800_000.000001, 1_751_800_000.002],
        ):
            first = composer.generate_tid()
            second = composer.generate_tid()
        for tid in (first, second):
            self.assertEqual(len(tid), 13)
            self.assertTrue(all(c in composer.TID_ALPHABET for c in tid))
        # Base32-sortable timestamps: later TIDs sort after earlier ones.
        self.assertLess(first, second)


@override_settings(MOSAIC_HOSTED=HOSTED_ON)
class PublishTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_publish_writes_document_and_invalidates_caches(self):
        tenant = _tenant()
        session = _oauth_session()
        cache.set("mosaic_atproto:collections:did:plc:alice", ["x"], 300)
        with (
            # ensure_publication: the publication record already exists.
            mock.patch(
                "django_mosaic.hosted.composer.xrpc_get",
                return_value={"value": {"url": "https://alice.mosaic.example"}},
            ),
            mock.patch("django_mosaic.atproto.oauth.flow.xrpc_call") as call,
        ):
            result = composer.publish(
                session, tenant, "  Hello  ", "Some **bold** text.", "A post"
            )

        body = call.call_args[1]["json_body"]
        record = body["record"]
        self.assertEqual(body["collection"], "site.standard.document")
        self.assertEqual(body["rkey"], result["rkey"])
        self.assertEqual(record["title"], "Hello")
        self.assertEqual(record["description"], "A post")
        self.assertEqual(record["path"], f"/posts/{result['rkey']}")
        self.assertEqual(
            record["site"], "at://did:plc:alice/site.standard.publication/self"
        )
        self.assertEqual(record["textContent"], "Some bold text.")
        self.assertEqual(record["content"][0]["markdown"], "Some **bold** text.")
        self.assertIn("publishedAt", record)
        self.assertEqual(result["url"], f"https://alice.mosaic.example{result['path']}")
        self.assertIsNone(cache.get("mosaic_atproto:collections:did:plc:alice"))

    def test_publish_creates_publication_when_missing(self):
        tenant = _tenant()
        session = _oauth_session()
        with (
            mock.patch(
                "django_mosaic.hosted.composer.xrpc_get",
                side_effect=AtprotoError("RecordNotFound"),
            ),
            mock.patch("django_mosaic.atproto.oauth.flow.xrpc_call") as call,
        ):
            composer.publish(session, tenant, "T", "body")
        # First write is the publication record, second the document.
        first = call.call_args_list[0][1]["json_body"]
        self.assertEqual(first["collection"], "site.standard.publication")
        self.assertEqual(first["rkey"], "self")
        self.assertEqual(first["record"]["url"], "https://alice.mosaic.example")

    def test_validation_errors(self):
        tenant = _tenant()
        session = _oauth_session()
        with self.assertRaisesRegex(composer.ComposerError, "title"):
            composer.publish(session, tenant, " ", "body")
        with self.assertRaisesRegex(composer.ComposerError, "Write something"):
            composer.publish(session, tenant, "T", "  ")
        with self.assertRaisesRegex(composer.ComposerError, "limited"):
            composer.publish(session, tenant, "T", "x" * 40_000)

    def test_site_url_prefers_verified_custom_domain(self):
        from django.utils import timezone

        tenant = _tenant(custom_domain="alice.blog")
        self.assertEqual(composer.site_url(tenant), "https://alice.mosaic.example")
        tenant.domain_verified_at = timezone.now()
        self.assertEqual(composer.site_url(tenant), "https://alice.blog")


class DocumentHelpersTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_document_markdown_extraction(self):
        value = {"content": [{"$type": "x", "markdown": "# hi"}]}
        self.assertEqual(composer.document_markdown(value), "# hi")
        self.assertIsNone(composer.document_markdown({"content": ["junk", {}]}))
        self.assertIsNone(composer.document_markdown({}))

    def test_get_document_caches_hit_and_miss(self):
        with mock.patch(
            "django_mosaic.hosted.composer.xrpc_get",
            return_value={"value": {"title": "T"}},
        ) as get:
            self.assertEqual(composer.get_document(ALICE, "abc")["title"], "T")
            composer.get_document(ALICE, "abc")
        get.assert_called_once()
        with mock.patch(
            "django_mosaic.hosted.composer.xrpc_get",
            side_effect=AtprotoError("nope"),
        ) as get:
            self.assertIsNone(composer.get_document(ALICE, "missing"))
            self.assertIsNone(composer.get_document(ALICE, "missing"))
        get.assert_called_once()


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__)
class WriteViewTest(TestCase):
    def test_anonymous_redirected_to_login(self):
        resp = self.client.get("/dashboard/write")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/oauth/login", resp["Location"])

    def test_get_renders_form(self):
        _sign_in(self.client)
        _tenant()
        resp = self.client.get("/dashboard/write")
        self.assertContains(resp, 'name="body"')

    def test_post_publishes(self):
        _sign_in(self.client)
        _tenant()
        published = {
            "rkey": "abc",
            "path": "/posts/abc",
            "url": "https://alice.mosaic.example/posts/abc",
        }
        with mock.patch.object(composer, "publish", return_value=published) as publish:
            resp = self.client.post(
                "/dashboard/write",
                {"title": "Hi", "description": "", "body": "text"},
            )
        self.assertContains(resp, "https://alice.mosaic.example/posts/abc")
        self.assertEqual(publish.call_args[0][2], "Hi")

    def test_post_error_rerenders_with_body_preserved(self):
        _sign_in(self.client)
        _tenant()
        with mock.patch.object(
            composer, "publish", side_effect=composer.ComposerError("too long")
        ):
            resp = self.client.post(
                "/dashboard/write", {"title": "Hi", "body": "my draft text"}
            )
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "too long", status_code=400)
        self.assertContains(resp, "my draft text", status_code=400)


@override_settings(
    MOSAIC_HOSTED=HOSTED_ON,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=TENANT_MIDDLEWARE,
)
class TenantDocumentPageTest(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, rkey="abc", value=None, settings_value=None):
        _tenant()
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch.object(composer, "get_document", return_value=value),
            mock.patch.object(site_settings, "load", return_value=settings_value),
        ):
            return self.client.get(f"/posts/{rkey}", HTTP_HOST="alice.mosaic.example")

    def test_renders_markdown_document(self):
        resp = self._get(
            value={
                "title": "Hello",
                "publishedAt": "2026-07-06T12:00:00Z",
                "textContent": "fallback",
                "content": [{"$type": "x", "markdown": "Some **bold** text."}],
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "<strong>bold</strong>")
        self.assertContains(resp, "2026-07-06")

    def test_falls_back_to_text_content(self):
        resp = self._get(value={"title": "T", "textContent": "plain words"})
        self.assertContains(resp, "plain words")

    def test_missing_document_404s(self):
        resp = self._get(value=None)
        self.assertEqual(resp.status_code, 404)

    def test_home_links_documents_to_local_pages(self):
        _tenant()
        built = [
            {
                "title": "Writing",
                "collection": "site.standard.document",
                "records": [
                    {"rkey": "abc", "uri": "", "cid": "", "value": {"title": "Hello"}}
                ],
                "record_template": [
                    "lexicons/site.standard.document.html",
                    "lexicons/generic.html",
                ],
            }
        ]
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch(
                "django_mosaic.atproto.preview.fetch_profile", return_value=None
            ),
            mock.patch(
                "django_mosaic.atproto.preview.build_sections",
                return_value=(built, []),
            ),
            mock.patch.object(site_settings, "load", return_value=None),
            mock.patch(
                "django_mosaic.atproto.lexicons.describe_repo",
                return_value=["site.standard.document"],
            ),
        ):
            resp = self.client.get("/", HTTP_HOST="alice.mosaic.example")
        self.assertContains(resp, 'href="/posts/abc"')


@override_settings(
    MOSAIC_HOSTED=HOSTED_ON,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=TENANT_MIDDLEWARE,
)
class CustomCssTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_served_as_css_with_nosniff(self):
        _tenant()
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch.object(
                site_settings,
                "load",
                return_value={"customCss": "body { border: 1px solid red; }"},
            ),
        ):
            resp = self.client.get("/custom.css", HTTP_HOST="alice.mosaic.example")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/css")
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertIn("border", resp.content.decode())

    def test_empty_when_unset(self):
        _tenant()
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch.object(site_settings, "load", return_value=None),
        ):
            resp = self.client.get("/custom.css", HTTP_HOST="alice.mosaic.example")
        self.assertEqual(resp.content, b"")

    def test_size_capped_and_type_checked(self):
        self.assertEqual(
            len(site_settings.custom_css({"customCss": "x" * 50_000})),
            site_settings.CUSTOM_CSS_MAX,
        )
        self.assertEqual(site_settings.custom_css({"customCss": ["not", "str"]}), "")

    def test_dashboard_save_includes_custom_css(self):
        _sign_in(self.client)
        _tenant()
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch.object(site_settings, "load", return_value=None),
            mock.patch("django_mosaic.atproto.lexicons.describe_repo", return_value=[]),
            mock.patch.object(site_settings, "save") as save,
        ):
            resp = self.client.post(
                "/dashboard",
                {"preset": "plain", "custom_css": "a { color: pink; }"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(save.call_args[1]["custom_css"], "a { color: pink; }")
