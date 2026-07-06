"""Tests for the hosted (multi-tenant) app: routing, tenant home, claiming.

The claim flow depends on the atproto OAuth layer, so this module skips
without the `oauth` extra (same pattern as the OAuth tests).
"""

from unittest import mock

import pytest

pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from django.test import TestCase, override_settings  # noqa: E402
from django.urls import include, path  # noqa: E402

from django_mosaic.atproto.identity import Identity  # noqa: E402
from django_mosaic.atproto.models import OAuthSession  # noqa: E402
from django_mosaic.atproto.oauth import flow  # noqa: E402
from django_mosaic.atproto.oauth import views as oauth_views  # noqa: E402
from django_mosaic.hosted import conf  # noqa: E402
from django_mosaic.hosted.middleware import TenantMiddleware  # noqa: E402
from django_mosaic.hosted.models import Tenant  # noqa: E402

HOSTED_ON = {"BASE_DOMAIN": "mosaic.example"}
ALICE = Identity("alice.example", "did:plc:alice", "https://pds.alice.example")

urlpatterns = [
    path("", include("django_mosaic.hosted.urls")),
    path("oauth/login", oauth_views.login, name="atproto-oauth-login"),
]


def _tenant(**overrides):
    fields = {
        "did": "did:plc:alice",
        "handle": "alice.example",
        "subdomain": "alice",
    }
    fields.update(overrides)
    return Tenant.objects.create(**fields)


def _oauth_session(did="did:plc:alice", handle="alice.example"):
    return OAuthSession.objects.create(
        did=did,
        handle=handle,
        pds_url="https://pds.alice.example",
        auth_server="https://auth.example",
        token_endpoint="https://auth.example/token",
        access_token="at-1",
        dpop_jwk={"kty": "EC"},
    )


@override_settings(MOSAIC_HOSTED=HOSTED_ON)
class ConfTest(TestCase):
    def test_disabled_without_base_domain(self):
        with override_settings(MOSAIC_HOSTED={}):
            self.assertFalse(conf.enabled())
            self.assertFalse(conf.claim_open())

    def test_enabled_and_open_by_default(self):
        self.assertTrue(conf.enabled())
        self.assertTrue(conf.claim_open())

    def test_reserved_merges_builtin_and_custom(self):
        with override_settings(
            MOSAIC_HOSTED={**HOSTED_ON, "RESERVED_SUBDOMAINS": ["Founder"]}
        ):
            reserved = conf.reserved_subdomains()
        self.assertIn("www", reserved)
        self.assertIn("oauth", reserved)
        self.assertIn("founder", reserved)


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"])
class MiddlewareTest(TestCase):
    def _run(self, host):
        captured = {}

        def view(request):
            captured["tenant"] = request.tenant
            captured["urlconf"] = getattr(request, "urlconf", None)
            return mock.Mock()

        middleware = TenantMiddleware(view)
        request = self.client.get("/", HTTP_HOST=host).wsgi_request
        middleware(request)
        return captured

    def test_base_domain_passes_through(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/", HTTP_HOST="mosaic.example")
        middleware = TenantMiddleware(lambda r: "ok")
        self.assertEqual(middleware(request), "ok")
        self.assertIsNone(request.tenant)
        self.assertFalse(hasattr(request, "urlconf"))

    def test_unrelated_host_passes_through(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/", HTTP_HOST="other.example")
        TenantMiddleware(lambda r: "ok")(request)
        self.assertIsNone(request.tenant)

    def test_tenant_host_sets_tenant_and_urlconf(self):
        from django.http import Http404
        from django.test import RequestFactory

        tenant = _tenant()
        request = RequestFactory().get("/", HTTP_HOST="alice.mosaic.example:8000")
        TenantMiddleware(lambda r: "ok")(request)
        self.assertEqual(request.tenant, tenant)
        self.assertEqual(request.urlconf, "django_mosaic.hosted.tenant_urls")

        # Unknown and suspended subdomains 404; nested subdomains rejected.
        for host in ("nobody.mosaic.example", "a.b.mosaic.example"):
            with self.assertRaises(Http404):
                TenantMiddleware(lambda r: "ok")(
                    RequestFactory().get("/", HTTP_HOST=host)
                )
        tenant.status = Tenant.STATUS_SUSPENDED
        tenant.save()
        with self.assertRaises(Http404):
            TenantMiddleware(lambda r: "ok")(
                RequestFactory().get("/", HTTP_HOST="alice.mosaic.example")
            )

    def test_disabled_never_matches(self):
        from django.test import RequestFactory

        _tenant()
        with override_settings(MOSAIC_HOSTED={}):
            request = RequestFactory().get("/", HTTP_HOST="alice.mosaic.example")
            TenantMiddleware(lambda r: "ok")(request)
        self.assertIsNone(request.tenant)


@override_settings(
    MOSAIC_HOSTED=HOSTED_ON,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django_mosaic.hosted.middleware.TenantMiddleware",
    ],
)
class TenantHomeTest(TestCase):
    def test_renders_indexable_home_from_pds(self):
        _tenant()
        profile = {"display_name": "Alice", "avatar": "", "description": "hi"}
        sections = [
            {
                "title": "Books",
                "collection": "buzz.bookhive.book",
                "records": [],
                "record_template": ["lexicons/generic.html"],
            }
        ]
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch(
                "django_mosaic.atproto.preview.fetch_profile", return_value=profile
            ),
            mock.patch(
                "django_mosaic.atproto.preview.build_sections",
                return_value=(sections, ["app.test.other"]),
            ),
            mock.patch("django_mosaic.hosted.site_settings.load", return_value=None),
            mock.patch(
                "django_mosaic.atproto.lexicons.describe_repo",
                return_value=["buzz.bookhive.book"],
            ),
        ):
            resp = self.client.get("/", HTTP_HOST="alice.mosaic.example")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Alice")
        self.assertContains(resp, "Books")
        self.assertNotContains(resp, 'content="noindex"')
        self.assertNotIn("X-Robots-Tag", resp)

    def test_unresolvable_handle_returns_503(self):
        from django_mosaic.atproto.client import AtprotoError

        _tenant()
        with mock.patch(
            "django_mosaic.atproto.identity.resolve",
            side_effect=AtprotoError("resolution failed"),
        ):
            resp = self.client.get("/", HTTP_HOST="alice.mosaic.example")
        self.assertEqual(resp.status_code, 503)

    def test_unknown_subdomain_404s(self):
        resp = self.client.get("/", HTTP_HOST="ghost.mosaic.example")
        self.assertEqual(resp.status_code, 404)


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__)
class ClaimTest(TestCase):
    def _sign_in(self):
        oauth = _oauth_session()
        session = self.client.session
        session[flow.DID_KEY] = oauth.did
        session.save()
        return oauth

    def test_anonymous_sees_login_link(self):
        resp = self.client.get("/claim")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/oauth/login?next=/claim")
        # An anonymous POST cannot claim.
        resp = self.client.post("/claim", {"subdomain": "alice"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Tenant.objects.count(), 0)

    def test_signed_in_claims_subdomain(self):
        self._sign_in()
        resp = self.client.post("/claim", {"subdomain": " Alice "})
        self.assertEqual(resp.status_code, 200)
        tenant = Tenant.objects.get()
        self.assertEqual(tenant.subdomain, "alice")
        self.assertEqual(tenant.did, "did:plc:alice")
        self.assertEqual(tenant.handle, "alice.example")
        self.assertContains(resp, "alice.mosaic.example")

    def test_form_suggests_handle_label(self):
        self._sign_in()
        resp = self.client.get("/claim")
        self.assertContains(resp, 'value="alice"')

    def test_invalid_reserved_and_taken_rejected(self):
        self._sign_in()
        for bad, message in [
            ("-alice", "lowercase"),
            ("oauth", "reserved"),
            ("", "Choose"),
        ]:
            resp = self.client.post("/claim", {"subdomain": bad})
            self.assertEqual(resp.status_code, 400, bad)
            self.assertContains(resp, message, status_code=400)
        _tenant(did="did:plc:other", handle="other.example", subdomain="taken")
        resp = self.client.post("/claim", {"subdomain": "taken"})
        self.assertContains(resp, "taken", status_code=400)
        self.assertFalse(Tenant.objects.filter(did="did:plc:alice").exists())

    def test_existing_tenant_shown_not_duplicated(self):
        self._sign_in()
        _tenant(subdomain="alice")
        resp = self.client.get("/claim")
        self.assertContains(resp, "alice.mosaic.example")
        resp = self.client.post("/claim", {"subdomain": "second"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Tenant.objects.count(), 1)

    def test_claims_closed(self):
        self._sign_in()
        with override_settings(MOSAIC_HOSTED={**HOSTED_ON, "CLAIM_OPEN": False}):
            resp = self.client.post("/claim", {"subdomain": "alice"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Tenant.objects.count(), 0)

    def test_claim_pinned_to_allowed_dids(self):
        self._sign_in()
        pinned = {**HOSTED_ON, "CLAIM_ALLOWED_DIDS": ["did:plc:owner"]}
        with override_settings(MOSAIC_HOSTED=pinned):
            resp = self.client.get("/claim")
            # The form is hidden for a disallowed DID, and a forged POST fails.
            self.assertNotContains(resp, "<form")
            resp = self.client.post("/claim", {"subdomain": "alice"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Tenant.objects.count(), 0)

        allowed = {**HOSTED_ON, "CLAIM_ALLOWED_DIDS": ["did:plc:alice"]}
        with override_settings(MOSAIC_HOSTED=allowed):
            resp = self.client.post("/claim", {"subdomain": "alice"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Tenant.objects.get().did, "did:plc:alice")

    def test_disabled_404s(self):
        with override_settings(MOSAIC_HOSTED={}):
            self.assertEqual(self.client.get("/claim").status_code, 404)
