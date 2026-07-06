"""Tests for the preview-service pieces: landing, waitlist, throttle, noindex."""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import path

from django_mosaic.atproto import conf, preview
from django_mosaic.atproto.models import WaitlistSignup
from django_mosaic.atproto.views import preview_landing, waitlist_signup

LANDING_ON = {**conf.as_dict(), "PREVIEW": True, "PREVIEW_LANDING": True}

# The landing/waitlist routes are built at import time from settings, so tests
# exercise the views through a private urlconf instead of the default one.
urlpatterns = [
    path("", preview_landing, name="atproto-preview-landing"),
    path("preview/waitlist", waitlist_signup, name="atproto-waitlist"),
    path("@<str:handle>", lambda r, handle: None, name="atproto-preview"),
]


@override_settings(MOSAIC_ATPROTO=LANDING_ON, ROOT_URLCONF=__name__)
class LandingPageTest(TestCase):
    def test_landing_renders_form_and_waitlist(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="handle"')
        self.assertContains(resp, "Join the waitlist")

    def test_handle_param_redirects_to_preview(self):
        resp = self.client.get("/", {"handle": "@Alice.Example.Com "})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/@alice.example.com")

    def test_waitlist_signup_stored_once(self):
        for _ in range(2):
            resp = self.client.post("/preview/waitlist", {"contact": "me@example.com"})
            self.assertEqual(resp.status_code, 302)
        self.assertEqual(WaitlistSignup.objects.count(), 1)
        self.assertContains(self.client.get("/?joined=1"), "on the list")

    def test_honeypot_drops_signup_silently(self):
        resp = self.client.post(
            "/preview/waitlist",
            {"contact": "bot@example.com", "website": "http://spam"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(WaitlistSignup.objects.count(), 0)

    def test_disabled_landing_404s(self):
        with override_settings(
            MOSAIC_ATPROTO={**conf.as_dict(), "PREVIEW_LANDING": False}
        ):
            self.assertEqual(self.client.get("/").status_code, 404)
            resp = self.client.post("/preview/waitlist", {"contact": "x@y.z"})
            self.assertEqual(resp.status_code, 404)


class ThrottleTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_allows_within_limit_blocks_over(self):
        with override_settings(
            MOSAIC_ATPROTO={**conf.as_dict(), "PREVIEW_RATE_LIMIT": 3}
        ):
            results = [preview.allow_request("10.0.0.1") for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])

    def test_ips_throttled_independently(self):
        with override_settings(
            MOSAIC_ATPROTO={**conf.as_dict(), "PREVIEW_RATE_LIMIT": 1}
        ):
            self.assertTrue(preview.allow_request("10.0.0.1"))
            self.assertFalse(preview.allow_request("10.0.0.1"))
            self.assertTrue(preview.allow_request("10.0.0.2"))

    def test_zero_disables_throttle(self):
        with override_settings(
            MOSAIC_ATPROTO={**conf.as_dict(), "PREVIEW_RATE_LIMIT": 0}
        ):
            results = [preview.allow_request("10.0.0.1") for _ in range(50)]
        self.assertTrue(all(results))


class PreviewHardeningTest(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(
        MOSAIC_ATPROTO={**conf.as_dict(), "PREVIEW": True, "PREVIEW_RATE_LIMIT": 1}
    )
    def test_preview_throttled_with_429(self):
        from django_mosaic.atproto.identity import Identity

        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve",
                return_value=Identity("a.example", "did:plc:a", "https://pds.a"),
            ),
            mock.patch(
                "django_mosaic.atproto.preview.fetch_profile", return_value=None
            ),
            mock.patch(
                "django_mosaic.atproto.preview.build_sections",
                return_value=([], []),
            ),
        ):
            first = self.client.get("/@a.example")
            second = self.client.get("/@a.example")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    @override_settings(MOSAIC_ATPROTO={**conf.as_dict(), "PREVIEW": True})
    def test_preview_is_noindex(self):
        from django_mosaic.atproto.identity import Identity

        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve",
                return_value=Identity("a.example", "did:plc:a", "https://pds.a"),
            ),
            mock.patch(
                "django_mosaic.atproto.preview.fetch_profile", return_value=None
            ),
            mock.patch(
                "django_mosaic.atproto.preview.build_sections",
                return_value=([], []),
            ),
        ):
            resp = self.client.get("/@a.example")
        self.assertEqual(resp["X-Robots-Tag"], "noindex")
        self.assertContains(resp, '<meta name="robots" content="noindex">')
