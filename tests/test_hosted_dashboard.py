"""Tests for M2c: site settings as PDS records, theme tokens, dashboard."""

from unittest import mock

import pytest

pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from django.core.cache import cache  # noqa: E402
from django.test import TestCase, override_settings  # noqa: E402
from django.urls import include, path  # noqa: E402

from django_mosaic.atproto.identity import Identity  # noqa: E402
from django_mosaic.atproto.models import OAuthSession  # noqa: E402
from django_mosaic.atproto.oauth import flow  # noqa: E402
from django_mosaic.atproto.oauth import views as oauth_views  # noqa: E402
from django_mosaic.hosted import site_settings  # noqa: E402
from django_mosaic.hosted.models import Tenant  # noqa: E402

HOSTED_ON = {"BASE_DOMAIN": "mosaic.example"}
ALICE = Identity("alice.example", "did:plc:alice", "https://pds.alice.example")

urlpatterns = [
    path("", include("django_mosaic.hosted.urls")),
    path("oauth/login", oauth_views.login, name="atproto-oauth-login"),
]

STORED = {
    "$type": site_settings.SETTINGS_NSID,
    "sections": [
        {"collection": "buzz.bookhive.book", "title": "Library", "enabled": True},
        {"collection": "sh.tangled.repo", "title": "Code", "enabled": False},
    ],
    "theme": {
        "preset": "paper",
        "tokens": {"accent": "#ff0000", "font": "mono", "radius": "large"},
    },
}


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


class ThemeValidationTest(TestCase):
    def test_clean_theme_keeps_valid_drops_invalid(self):
        theme = site_settings.clean_theme(
            "paper",
            {
                "accent": "#12abCD",
                "background": "url(javascript:alert(1))",
                "text": "#fff",
                "font": "comic-sans",
                "radius": "large",
            },
        )
        self.assertEqual(theme["preset"], "paper")
        self.assertEqual(theme["tokens"]["accent"], "#12abCD")
        self.assertEqual(theme["tokens"]["text"], "#fff")
        # Invalid values fall back to the preset's token (paper has one)...
        self.assertNotIn("url(", theme["tokens"].get("background", ""))
        # ...and invalid enums are dropped entirely or preset-backed.
        self.assertNotEqual(theme["tokens"].get("font"), "comic-sans")
        self.assertEqual(theme["tokens"]["radius"], "large")

    def test_unknown_preset_falls_back_to_plain(self):
        theme = site_settings.clean_theme("hax", None)
        self.assertEqual(theme["preset"], "plain")

    def test_css_variables_revalidates_stored_record(self):
        css = site_settings.css_variables(
            {"theme": {"preset": "plain", "tokens": {"accent": "#0f0f0f"}}}
        )
        self.assertEqual(css, "--mosaic-accent:#0f0f0f;")
        # A hostile stored record cannot smuggle CSS through.
        css = site_settings.css_variables(
            {"theme": {"tokens": {"accent": "red;}body{display:none"}}}
        )
        self.assertNotIn("display", css)

    def test_css_variables_maps_enums(self):
        css = site_settings.css_variables(
            {"theme": {"tokens": {"font": "mono", "radius": "small"}}}
        )
        self.assertIn("--mosaic-font:", css)
        self.assertIn("monospace", css)
        self.assertIn("--mosaic-radius:4px;", css)

    def test_empty_settings_produce_no_css(self):
        self.assertEqual(site_settings.css_variables(None), "")

    def test_hostile_record_shapes_do_not_crash(self):
        # A tenant's own repo can hold any shape; the render helpers must
        # degrade to defaults instead of raising (which would 500 their site).
        for hostile in (
            {"sections": "not-a-list", "theme": "not-a-dict"},
            {"sections": [1, "x", None], "theme": {"tokens": "nope"}},
            "the-whole-record-is-a-string",
            [1, 2, 3],
            {"theme": {"preset": ["list"], "tokens": ["list"]}},
        ):
            self.assertIsInstance(site_settings.css_variables(hostile), str)
            self.assertIsInstance(site_settings.custom_css(hostile), str)
            with mock.patch(
                "django_mosaic.atproto.lexicons.describe_repo", return_value=[]
            ):
                self.assertIsInstance(
                    site_settings.effective_sections(ALICE, hostile), list
                )


class SettingsRecordTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_load_fetches_and_caches(self):
        with mock.patch(
            "django_mosaic.hosted.site_settings.xrpc_get",
            return_value={"value": STORED},
        ) as get:
            first = site_settings.load(ALICE)
            second = site_settings.load(ALICE)
        self.assertEqual(first["theme"]["preset"], "paper")
        self.assertEqual(second, first)
        get.assert_called_once()
        params = get.call_args[0][2]
        self.assertEqual(params["collection"], site_settings.SETTINGS_NSID)
        self.assertEqual(params["rkey"], site_settings.SETTINGS_RKEY)

    def test_load_miss_is_cached_as_none(self):
        from django_mosaic.atproto.client import AtprotoError

        with mock.patch(
            "django_mosaic.hosted.site_settings.xrpc_get",
            side_effect=AtprotoError("RecordNotFound"),
        ) as get:
            self.assertIsNone(site_settings.load(ALICE))
            self.assertIsNone(site_settings.load(ALICE))
        get.assert_called_once()

    def test_save_puts_record_and_invalidates_cache(self):
        oauth = mock.Mock(did="did:plc:alice")
        cache.set(f"mosaic_hosted:settings:{oauth.did}", {"stale": True}, 300)
        with mock.patch("django_mosaic.atproto.oauth.flow.xrpc_call") as call:
            record = site_settings.save(
                oauth, [{"collection": "x", "title": "X", "enabled": True}], {}
            )
        body = call.call_args[1]["json_body"]
        self.assertEqual(call.call_args[0][1], "com.atproto.repo.putRecord")
        self.assertEqual(body["collection"], site_settings.SETTINGS_NSID)
        self.assertEqual(body["rkey"], "self")
        self.assertEqual(body["record"]["$type"], site_settings.SETTINGS_NSID)
        self.assertIn("updatedAt", record)
        self.assertIsNone(cache.get(f"mosaic_hosted:settings:{oauth.did}"))

    def test_effective_sections_merges_stored_and_new(self):
        with mock.patch(
            "django_mosaic.atproto.lexicons.describe_repo",
            return_value=["buzz.bookhive.book", "sh.tangled.repo"],
        ):
            merged = site_settings.effective_sections(ALICE, STORED)
        by_collection = {s["collection"]: s for s in merged}
        self.assertEqual(by_collection["buzz.bookhive.book"]["title"], "Library")
        self.assertFalse(by_collection["sh.tangled.repo"]["enabled"])

    def test_arrange_orders_filters_and_retitles(self):
        built = [
            {"collection": "sh.tangled.repo", "title": "Projects", "records": [1]},
            {"collection": "buzz.bookhive.book", "title": "Books", "records": [2]},
            {"collection": "new.thing", "title": "New", "records": [3]},
        ]
        config = [
            {"collection": "buzz.bookhive.book", "title": "Library", "enabled": True},
            {"collection": "sh.tangled.repo", "title": "Code", "enabled": False},
        ]
        arranged = site_settings.arrange(built, config)
        self.assertEqual(
            [s["title"] for s in arranged], ["Library", "New"]
        )  # disabled dropped, unconfigured appended


@override_settings(MOSAIC_HOSTED=HOSTED_ON, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__)
class DashboardViewTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/oauth/login", resp["Location"])

    def test_without_tenant_redirected_to_claim(self):
        _sign_in(self.client)
        resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/claim")

    def test_suspended_tenant_locked_out_of_dashboard_paths(self):
        # Suspension must block writes, not just public serving: a suspended
        # tenant may not reach the dashboard, composer, or domain settings.
        _sign_in(self.client)
        Tenant.objects.create(
            did="did:plc:alice",
            handle="alice.example",
            subdomain="alice",
            status=Tenant.STATUS_SUSPENDED,
        )
        self.assertEqual(self.client.get("/dashboard").status_code, 403)
        self.assertEqual(self.client.get("/dashboard/write").status_code, 403)
        resp = self.client.post("/dashboard/domain", {"domain": "evil.blog"})
        self.assertEqual(resp.status_code, 403)

    def _with_tenant(self):
        _sign_in(self.client)
        return Tenant.objects.create(
            did="did:plc:alice", handle="alice.example", subdomain="alice"
        )

    def test_get_renders_current_settings(self):
        self._with_tenant()
        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve_did", return_value=ALICE
            ),
            mock.patch("django_mosaic.hosted.site_settings.load", return_value=STORED),
            mock.patch(
                "django_mosaic.atproto.lexicons.describe_repo",
                return_value=["buzz.bookhive.book"],
            ),
        ):
            resp = self.client.get("/dashboard")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="Library"')
        self.assertContains(resp, "buzz.bookhive.book")

    def test_post_saves_to_pds_and_redirects(self):
        self._with_tenant()
        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve_did", return_value=ALICE
            ),
            mock.patch("django_mosaic.hosted.site_settings.load", return_value=None),
            mock.patch("django_mosaic.hosted.site_settings.save") as save,
        ):
            resp = self.client.post(
                "/dashboard",
                {
                    "collection": ["sh.tangled.repo", "buzz.bookhive.book"],
                    "position:sh.tangled.repo": "2",
                    "position:buzz.bookhive.book": "1",
                    "title:sh.tangled.repo": "Code",
                    "title:buzz.bookhive.book": "  Library  ",
                    "enabled:buzz.bookhive.book": "on",
                    "preset": "night",
                    "token-accent": "#ff0000",
                    "token-font": "serif",
                    "token-radius": "none",
                },
            )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("saved=1", resp["Location"])
        sections, theme = save.call_args[0][1], save.call_args[0][2]
        self.assertEqual(
            [s["collection"] for s in sections],
            ["buzz.bookhive.book", "sh.tangled.repo"],  # ordered by position
        )
        self.assertEqual(sections[0]["title"], "Library")
        self.assertTrue(sections[0]["enabled"])
        self.assertFalse(sections[1]["enabled"])  # checkbox absent
        self.assertEqual(theme["preset"], "night")
        self.assertEqual(theme["tokens"]["accent"], "#ff0000")
        self.assertEqual(theme["tokens"]["font"], "serif")

    def test_post_save_failure_shows_error(self):
        self._with_tenant()
        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve_did", return_value=ALICE
            ),
            mock.patch("django_mosaic.hosted.site_settings.load", return_value=None),
            mock.patch(
                "django_mosaic.hosted.site_settings.save",
                side_effect=flow.OAuthError("token expired"),
            ),
        ):
            resp = self.client.post("/dashboard", {"preset": "plain"})
        self.assertEqual(resp.status_code, 502)
        self.assertContains(resp, "token expired", status_code=502)


@override_settings(
    MOSAIC_HOSTED=HOSTED_ON,
    ALLOWED_HOSTS=["*"],
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django_mosaic.hosted.middleware.TenantMiddleware",
    ],
)
class TenantHomeSettingsTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_home_applies_stored_settings(self):
        Tenant.objects.create(
            did="did:plc:alice", handle="alice.example", subdomain="alice"
        )
        built = [
            {
                "title": "Books",
                "collection": "buzz.bookhive.book",
                "records": [{"value": {}}],
                "record_template": ["lexicons/generic.html"],
            },
            {
                "title": "Projects",
                "collection": "sh.tangled.repo",
                "records": [{"value": {}}],
                "record_template": ["lexicons/generic.html"],
            },
        ]
        with (
            mock.patch(
                "django_mosaic.atproto.identity.resolve_did", return_value=ALICE
            ),
            mock.patch(
                "django_mosaic.atproto.preview.fetch_profile", return_value=None
            ),
            mock.patch(
                "django_mosaic.atproto.preview.build_sections",
                return_value=(built, []),
            ),
            mock.patch("django_mosaic.hosted.site_settings.load", return_value=STORED),
            mock.patch(
                "django_mosaic.atproto.lexicons.describe_repo",
                return_value=["buzz.bookhive.book", "sh.tangled.repo"],
            ),
        ):
            resp = self.client.get("/", HTTP_HOST="alice.mosaic.example")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Library")  # retitled
        self.assertNotContains(resp, "Projects")  # disabled section dropped
        self.assertContains(resp, "--mosaic-accent:#ff0000;")
        self.assertContains(resp, "monospace")  # font token mapped to a stack
