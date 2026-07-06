"""Tests for M3: custom domains, on-demand TLS ask, domain-as-handle, reports."""

import pytest

pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from django.core.cache import cache  # noqa: E402
from django.test import RequestFactory, TestCase, override_settings  # noqa: E402
from django.urls import include, path  # noqa: E402

from django_mosaic.atproto.identity import Identity  # noqa: E402
from django_mosaic.atproto.models import OAuthSession  # noqa: E402
from django_mosaic.atproto.oauth import flow  # noqa: E402
from django_mosaic.atproto.oauth import views as oauth_views  # noqa: E402
from django_mosaic.hosted.middleware import TenantMiddleware  # noqa: E402
from django_mosaic.hosted.models import Report, Tenant  # noqa: E402

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


def _sign_in(client):
    oauth = OAuthSession.objects.create(
        did="did:plc:alice",
        handle="alice.example",
        pds_url="https://pds.alice.example",
        auth_server="https://auth.example",
        token_endpoint="https://auth.example/token",
        access_token="at-1",
        dpop_jwk={"kty": "EC"},
    )
    session = client.session
    session[flow.DID_KEY] = oauth.did
    session.save()
    return oauth


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"])
class CustomDomainRoutingTest(TestCase):
    def test_custom_domain_routes_and_verifies(self):
        tenant = _tenant(custom_domain="alice.blog")
        self.assertIsNone(tenant.domain_verified_at)
        request = RequestFactory().get("/", HTTP_HOST="alice.blog")
        TenantMiddleware(lambda r: "ok")(request)
        self.assertEqual(request.tenant, tenant)
        self.assertEqual(request.urlconf, "django_mosaic.hosted.tenant_urls")
        tenant.refresh_from_db()
        self.assertIsNotNone(tenant.domain_verified_at)
        # A second request does not re-stamp.
        stamp = tenant.domain_verified_at
        TenantMiddleware(lambda r: "ok")(
            RequestFactory().get("/", HTTP_HOST="alice.blog")
        )
        tenant.refresh_from_db()
        self.assertEqual(tenant.domain_verified_at, stamp)

    def test_suspended_custom_domain_404s(self):
        from django.http import Http404

        _tenant(custom_domain="alice.blog", status=Tenant.STATUS_SUSPENDED)
        with self.assertRaises(Http404):
            TenantMiddleware(lambda r: "ok")(
                RequestFactory().get("/", HTTP_HOST="alice.blog")
            )

    def test_unknown_external_host_passes_through(self):
        request = RequestFactory().get("/", HTTP_HOST="stranger.example")
        self.assertEqual(TenantMiddleware(lambda r: "ok")(request), "ok")
        self.assertIsNone(request.tenant)


@override_settings(
    MOSAIC_HOSTED=HOSTED_ON,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=TENANT_MIDDLEWARE,
)
class WellKnownDidTest(TestCase):
    def test_serves_tenant_did_on_tenant_host(self):
        _tenant(custom_domain="alice.blog")
        resp = self.client.get("/.well-known/atproto-did", HTTP_HOST="alice.blog")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "did:plc:alice")
        self.assertEqual(resp["Content-Type"], "text/plain")
        # Subdomain hosts serve it too.
        resp = self.client.get(
            "/.well-known/atproto-did", HTTP_HOST="alice.mosaic.example"
        )
        self.assertEqual(resp.content.decode(), "did:plc:alice")


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__)
class DomainCheckTest(TestCase):
    def test_registered_active_domain_ok(self):
        _tenant(custom_domain="alice.blog")
        resp = self.client.get("/domains/check", {"domain": "Alice.Blog."})
        self.assertEqual(resp.status_code, 200)

    def test_unknown_or_suspended_refused(self):
        self.assertEqual(
            self.client.get("/domains/check", {"domain": "ghost.blog"}).status_code,
            404,
        )
        self.assertEqual(self.client.get("/domains/check").status_code, 404)
        _tenant(custom_domain="alice.blog", status=Tenant.STATUS_SUSPENDED)
        resp = self.client.get("/domains/check", {"domain": "alice.blog"})
        self.assertEqual(resp.status_code, 404)

    def test_base_domain_ok(self):
        resp = self.client.get("/domains/check", {"domain": "mosaic.example"})
        self.assertEqual(resp.status_code, 200)

    def test_active_tenant_subdomain_ok(self):
        _tenant()
        resp = self.client.get("/domains/check", {"domain": "alice.mosaic.example"})
        self.assertEqual(resp.status_code, 200)

    def test_unclaimed_suspended_or_nested_subdomain_refused(self):
        _tenant(status=Tenant.STATUS_SUSPENDED)
        for host in (
            "ghost.mosaic.example",
            "alice.mosaic.example",  # suspended
            "deep.alice.mosaic.example",  # nested labels are never tenant hosts
        ):
            resp = self.client.get("/domains/check", {"domain": host})
            self.assertEqual(resp.status_code, 404, host)


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__)
class DashboardDomainTest(TestCase):
    def _tenant_signed_in(self):
        _sign_in(self.client)
        return _tenant()

    def test_set_domain(self):
        tenant = self._tenant_signed_in()
        resp = self.client.post("/dashboard/domain", {"domain": " Alice.Blog. "})
        self.assertEqual(resp.status_code, 302)
        tenant.refresh_from_db()
        self.assertEqual(tenant.custom_domain, "alice.blog")
        self.assertIsNone(tenant.domain_verified_at)

    def test_remove_domain(self):
        tenant = self._tenant_signed_in()
        tenant.custom_domain = "alice.blog"
        tenant.save()
        resp = self.client.post("/dashboard/domain", {"remove": "1"})
        self.assertEqual(resp.status_code, 302)
        tenant.refresh_from_db()
        self.assertIsNone(tenant.custom_domain)

    def test_invalid_and_verified_taken_domains_rejected(self):
        from django.utils import timezone

        tenant = self._tenant_signed_in()
        # A domain another tenant has *verified* is locked and cannot be taken.
        _tenant(
            did="did:plc:other",
            handle="other.example",
            subdomain="other",
            custom_domain="taken.blog",
            domain_verified_at=timezone.now(),
        )
        for bad in (
            "not a domain",
            "sub.mosaic.example",
            "mosaic.example",
            "taken.blog",
        ):
            resp = self.client.post("/dashboard/domain", {"domain": bad})
            self.assertEqual(resp.status_code, 302, bad)
            tenant.refresh_from_db()
            self.assertIsNone(tenant.custom_domain, bad)

    def test_unverified_domain_can_be_reclaimed(self):
        # A squatter registering a string they don't control (unverified) must
        # not permanently block the real owner from connecting it.
        squatter = _tenant(
            did="did:plc:squatter",
            handle="squatter.example",
            subdomain="squatter",
            custom_domain="contested.blog",
        )
        tenant = self._tenant_signed_in()
        resp = self.client.post("/dashboard/domain", {"domain": "contested.blog"})
        self.assertEqual(resp.status_code, 302)
        tenant.refresh_from_db()
        squatter.refresh_from_db()
        self.assertEqual(tenant.custom_domain, "contested.blog")
        self.assertIsNone(squatter.custom_domain)  # reclaimed

    def test_anonymous_redirected(self):
        resp = self.client.post("/dashboard/domain", {"domain": "a.blog"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/claim")


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__)
class ReportTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_report_by_subdomain_and_domain(self):
        _tenant(custom_domain="alice.blog")
        for site in ("alice", "alice.blog"):
            resp = self.client.post("/report", {"site": site, "reason": "spam content"})
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "received")
        self.assertEqual(Report.objects.count(), 2)

    def test_unknown_site_and_empty_reason_rejected(self):
        _tenant()
        resp = self.client.post("/report", {"site": "ghost", "reason": "x"})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/report", {"site": "alice", "reason": "  "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Report.objects.count(), 0)

    def test_honeypot_pretends_success(self):
        _tenant()
        resp = self.client.post(
            "/report", {"site": "alice", "reason": "spam", "website": "http://x"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "received")
        self.assertEqual(Report.objects.count(), 0)

    def test_throttled_after_limit(self):
        from django_mosaic.hosted import views

        _tenant()
        for i in range(views.REPORTS_PER_HOUR + 3):
            self.client.post("/report", {"site": "alice", "reason": f"r{i}"})
        self.assertEqual(Report.objects.count(), views.REPORTS_PER_HOUR)

    def test_get_renders_form_prefilled(self):
        _tenant()
        resp = self.client.get("/report", {"site": "alice"})
        self.assertContains(resp, 'value="alice"')
