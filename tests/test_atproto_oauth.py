"""Tests for the ATProto OAuth client (metadata, DPoP, PAR flow, XRPC).

All HTTP is mocked; the `oauth` extra (pyjwt + cryptography) is required and
the module is skipped when it is missing, mirroring the deploy-extra pattern.
"""

from unittest import mock

import pytest

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from django.test import RequestFactory, TestCase, override_settings  # noqa: E402
from django.urls import path  # noqa: E402
from django.utils import timezone  # noqa: E402

from django_mosaic.atproto import conf  # noqa: E402
from django_mosaic.atproto.identity import Identity  # noqa: E402
from django_mosaic.atproto.models import OAuthSession  # noqa: E402
from django_mosaic.atproto.oauth import dpop, flow, keys, metadata  # noqa: E402
from django_mosaic.atproto.oauth import views as oauth_views  # noqa: E402

TEST_KEY_PEM = keys.generate_private_key_pem()
OAUTH_ON = {
    **conf.as_dict(),
    "OAUTH_CLIENT": {
        "BASE_URL": "https://client.example",
        "PRIVATE_KEY": TEST_KEY_PEM,
        "KEY_ID": "test-key-1",
    },
}
ISSUER = "https://auth.example"
SERVER_METADATA = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "pushed_authorization_request_endpoint": f"{ISSUER}/par",
}
ALICE = Identity("alice.example", "did:plc:alice", "https://pds.alice.example")

# The OAuth routes are built at import time from settings, so tests exercise
# the views through a private urlconf (same pattern as the landing tests).
urlpatterns = [
    path(
        "oauth/client-metadata.json",
        oauth_views.client_metadata,
        name="atproto-oauth-client-metadata",
    ),
    path("oauth/jwks.json", oauth_views.jwks, name="atproto-oauth-jwks"),
    path("oauth/login", oauth_views.login, name="atproto-oauth-login"),
    path("oauth/callback", oauth_views.callback, name="atproto-oauth-callback"),
    path("oauth/logout", oauth_views.logout, name="atproto-oauth-logout"),
]


def _resp(json_data=None, status=200, headers=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.headers = headers or {}
    resp.text = ""
    resp.content = b"{}" if json_data is not None else b""
    if json_data is None:
        resp.json.side_effect = ValueError("no body")
    else:
        resp.json.return_value = json_data
    return resp


def _fake_request(session=None, get_params=None, post_params=None):
    factory = RequestFactory()
    if post_params is not None:
        request = factory.post("/oauth/login", post_params)
    else:
        request = factory.get("/oauth/callback", get_params or {})
    request.session = session if session is not None else {}
    return request


@override_settings(MOSAIC_ATPROTO=OAUTH_ON)
class ConfMetadataTest(TestCase):
    def test_disabled_by_default(self):
        with override_settings(MOSAIC_ATPROTO={**conf.DEFAULTS}):
            self.assertFalse(conf.oauth_enabled())

    def test_enabled_with_base_url_and_key(self):
        self.assertTrue(conf.oauth_enabled())
        # Partial OAUTH_CLIENT dicts pick up defaults for the rest.
        self.assertEqual(conf.oauth_client()["SCOPE"], "atproto transition:generic")

    def test_client_metadata_document(self):
        doc = metadata.client_metadata()
        self.assertEqual(
            doc["client_id"], "https://client.example/oauth/client-metadata.json"
        )
        self.assertEqual(
            doc["redirect_uris"], ["https://client.example/oauth/callback"]
        )
        self.assertEqual(doc["token_endpoint_auth_method"], "private_key_jwt")
        self.assertEqual(doc["token_endpoint_auth_signing_alg"], "ES256")
        self.assertTrue(doc["dpop_bound_access_tokens"])
        self.assertEqual(doc["grant_types"], ["authorization_code", "refresh_token"])
        self.assertEqual(doc["jwks_uri"], "https://client.example/oauth/jwks.json")

    def test_jwks_publishes_public_key_only(self):
        jwks = keys.client_jwks()
        (jwk,) = jwks["keys"]
        self.assertEqual(jwk["kty"], "EC")
        self.assertEqual(jwk["crv"], "P-256")
        self.assertEqual(jwk["kid"], "test-key-1")
        self.assertNotIn("d", jwk)

    def test_rejects_non_p256_key(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        wrong = ec.generate_private_key(ec.SECP384R1())
        pem = wrong.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        with override_settings(
            MOSAIC_ATPROTO={
                **OAUTH_ON,
                "OAUTH_CLIENT": {**OAUTH_ON["OAUTH_CLIENT"], "PRIVATE_KEY": pem},
            }
        ):
            with self.assertRaises(keys.OAuthConfigError):
                keys.load_client_key()


class DpopTest(TestCase):
    def test_proof_shape_and_signature(self):
        jwk = dpop.generate_key()
        token = dpop.proof(
            jwk,
            "post",
            "https://pds.example/xrpc/foo?bar=1",
            nonce="n-123",
            access_token="secret-token",
        )
        header = jwt.get_unverified_header(token)
        self.assertEqual(header["typ"], "dpop+jwt")
        self.assertEqual(header["alg"], "ES256")
        self.assertNotIn("d", header["jwk"])

        public_key = dpop._load_private_jwk(jwk).public_key()
        claims = jwt.decode(token, public_key, algorithms=["ES256"])
        self.assertEqual(claims["htm"], "POST")
        # Query and fragment are stripped from htu per RFC 9449.
        self.assertEqual(claims["htu"], "https://pds.example/xrpc/foo")
        self.assertEqual(claims["nonce"], "n-123")
        self.assertIn("jti", claims)
        self.assertIn("iat", claims)
        # ath = b64url(sha256(access token))
        import base64
        import hashlib

        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(b"secret-token").digest())
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(claims["ath"], expected)

    def test_proofs_have_unique_jti(self):
        jwk = dpop.generate_key()
        tokens = {
            jwt.decode(
                dpop.proof(jwk, "GET", "https://x.example/y"),
                options={"verify_signature": False},
            )["jti"]
            for _ in range(5)
        }
        self.assertEqual(len(tokens), 5)


@override_settings(MOSAIC_ATPROTO=OAUTH_ON)
class DiscoveryTest(TestCase):
    def test_authorization_server_for(self):
        with mock.patch.object(flow, "requests") as m:
            m.get.return_value = _resp({"authorization_servers": [ISSUER + "/"]})
            self.assertEqual(flow.authorization_server_for("https://pds.x/"), ISSUER)
            m.get.assert_called_once()
            self.assertIn(
                "https://pds.x/.well-known/oauth-protected-resource",
                m.get.call_args[0],
            )

    def test_missing_authorization_servers_raises(self):
        with mock.patch.object(flow, "requests") as m:
            m.get.return_value = _resp({})
            with self.assertRaises(flow.OAuthError):
                flow.authorization_server_for("https://pds.x")

    def test_issuer_mismatch_rejected(self):
        with mock.patch.object(flow, "requests") as m:
            m.get.return_value = _resp({**SERVER_METADATA, "issuer": "https://evil"})
            with self.assertRaises(flow.OAuthError):
                flow.authorization_server_metadata(ISSUER)

    def test_internal_authorization_server_rejected(self):
        # SSRF guard: an attacker-served protected-resource doc pointing the
        # "authorization server" at an internal host must be refused.
        with mock.patch.object(flow, "requests") as m:
            m.get.return_value = _resp(
                {"authorization_servers": ["http://169.254.169.254"]}
            )
            with self.assertRaises(flow.OAuthError):
                flow.authorization_server_for("https://pds.x")

    def test_internal_endpoint_in_metadata_rejected(self):
        # Even with a matching issuer, an internal token/authorize endpoint
        # is refused before the client ever POSTs a client assertion to it.
        with mock.patch.object(flow, "requests") as m:
            m.get.return_value = _resp(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": "http://localhost:9000/token",
                }
            )
            with self.assertRaises(flow.OAuthError):
                flow.authorization_server_metadata(ISSUER)


@override_settings(MOSAIC_ATPROTO=OAUTH_ON)
class StartAuthTest(TestCase):
    def _run(self, post_responses):
        request = _fake_request(session={})
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch.object(flow, "requests") as m,
        ):
            m.get.side_effect = [
                _resp({"authorization_servers": [ISSUER]}),
                _resp(SERVER_METADATA),
            ]
            m.post.side_effect = post_responses
            url = flow.start_auth(request, "alice.example")
        return request, url, m

    def test_full_par_flow(self):
        request, url, m = self._run(
            [_resp({"request_uri": "urn:example:req-1", "expires_in": 60})]
        )
        self.assertTrue(url.startswith(f"{ISSUER}/authorize?"))
        self.assertIn("request_uri=urn%3Aexample%3Areq-1", url)
        self.assertIn(
            "client_id=https%3A%2F%2Fclient.example%2Foauth%2Fclient-metadata.json",
            url,
        )

        par_call = m.post.call_args
        self.assertEqual(par_call[0][0], f"{ISSUER}/par")
        body = par_call[1]["data"]
        self.assertEqual(body["code_challenge_method"], "S256")
        self.assertEqual(body["login_hint"], "alice.example")
        self.assertEqual(body["scope"], "atproto transition:generic")
        # The client assertion is a valid ES256 JWT addressed to the issuer.
        assertion = jwt.decode(
            body["client_assertion"], options={"verify_signature": False}
        )
        self.assertEqual(assertion["aud"], ISSUER)
        self.assertEqual(assertion["iss"], metadata.client_id())
        # A DPoP proof accompanied the PAR request.
        self.assertIn("DPoP", par_call[1]["headers"])

        pending = request.session[flow.PENDING_KEY]
        self.assertEqual(pending["did"], "did:plc:alice")
        self.assertEqual(pending["token_endpoint"], f"{ISSUER}/token")
        # PKCE challenge in the request matches the stored verifier.
        import base64
        import hashlib

        expected = (
            base64.urlsafe_b64encode(
                hashlib.sha256(pending["verifier"].encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        self.assertEqual(body["code_challenge"], expected)

    def test_dpop_nonce_retry(self):
        request, url, m = self._run(
            [
                _resp(
                    {"error": "use_dpop_nonce"},
                    status=400,
                    headers={"DPoP-Nonce": "server-nonce"},
                ),
                _resp({"request_uri": "urn:example:req-2", "expires_in": 60}),
            ]
        )
        self.assertEqual(m.post.call_count, 2)
        retry_proof = m.post.call_args_list[1][1]["headers"]["DPoP"]
        claims = jwt.decode(retry_proof, options={"verify_signature": False})
        self.assertEqual(claims["nonce"], "server-nonce")
        # The server nonce is carried into the pending state for the token call.
        self.assertEqual(
            request.session[flow.PENDING_KEY]["dpop_nonce"], "server-nonce"
        )

    def test_par_unsupported_raises(self):
        request = _fake_request(session={})
        with (
            mock.patch("django_mosaic.atproto.identity.resolve", return_value=ALICE),
            mock.patch.object(flow, "requests") as m,
        ):
            m.get.side_effect = [
                _resp({"authorization_servers": [ISSUER]}),
                _resp(
                    {
                        k: v
                        for k, v in SERVER_METADATA.items()
                        if k != "pushed_authorization_request_endpoint"
                    }
                ),
            ]
            with self.assertRaises(flow.OAuthError):
                flow.start_auth(request, "alice.example")


def _pending(**overrides):
    pending = {
        "state": "state-123",
        "verifier": "verifier-123",
        "dpop_jwk": dpop.generate_key(),
        "dpop_nonce": None,
        "issuer": ISSUER,
        "token_endpoint": f"{ISSUER}/token",
        "did": "did:plc:alice",
        "handle": "alice.example",
        "pds_url": "https://pds.alice.example",
    }
    pending.update(overrides)
    return pending


TOKENS = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "sub": "did:plc:alice",
    "scope": "atproto transition:generic",
    "expires_in": 3600,
    "token_type": "DPoP",
}


@override_settings(MOSAIC_ATPROTO=OAUTH_ON)
class CompleteAuthTest(TestCase):
    def test_exchanges_code_and_persists_session(self):
        request = _fake_request(
            session={flow.PENDING_KEY: _pending()},
            get_params={"code": "code-1", "state": "state-123", "iss": ISSUER},
        )
        with mock.patch.object(flow, "requests") as m:
            m.post.return_value = _resp(TOKENS)
            session = flow.complete_auth(request)

        body = m.post.call_args[1]["data"]
        self.assertEqual(body["grant_type"], "authorization_code")
        self.assertEqual(body["code"], "code-1")
        self.assertEqual(body["code_verifier"], "verifier-123")

        self.assertEqual(session.did, "did:plc:alice")
        self.assertEqual(session.access_token, "at-1")
        self.assertEqual(session.refresh_token, "rt-1")
        self.assertIsNotNone(session.access_token_expires_at)
        self.assertEqual(request.session[flow.DID_KEY], "did:plc:alice")
        self.assertNotIn(flow.PENDING_KEY, request.session)
        self.assertEqual(OAuthSession.objects.count(), 1)

    def test_state_mismatch_rejected(self):
        request = _fake_request(
            session={flow.PENDING_KEY: _pending()},
            get_params={"code": "code-1", "state": "attacker-state"},
        )
        with self.assertRaisesRegex(flow.OAuthError, "State mismatch"):
            flow.complete_auth(request)
        self.assertEqual(OAuthSession.objects.count(), 0)

    def test_issuer_mismatch_rejected(self):
        request = _fake_request(
            session={flow.PENDING_KEY: _pending()},
            get_params={"code": "c", "state": "state-123", "iss": "https://evil"},
        )
        with self.assertRaisesRegex(flow.OAuthError, "Issuer mismatch"):
            flow.complete_auth(request)

    def test_sub_mismatch_rejected(self):
        request = _fake_request(
            session={flow.PENDING_KEY: _pending()},
            get_params={"code": "c", "state": "state-123", "iss": ISSUER},
        )
        with mock.patch.object(flow, "requests") as m:
            m.post.return_value = _resp({**TOKENS, "sub": "did:plc:mallory"})
            with self.assertRaisesRegex(flow.OAuthError, "not the expected account"):
                flow.complete_auth(request)
        self.assertEqual(OAuthSession.objects.count(), 0)

    def test_missing_iss_rejected(self):
        request = _fake_request(
            session={flow.PENDING_KEY: _pending()},
            get_params={"code": "c", "state": "state-123"},
        )
        with self.assertRaisesRegex(flow.OAuthError, "missing the required `iss`"):
            flow.complete_auth(request)
        self.assertEqual(OAuthSession.objects.count(), 0)

    def test_upstream_error_surfaces(self):
        request = _fake_request(
            session={flow.PENDING_KEY: _pending()},
            get_params={"error": "access_denied", "state": "state-123"},
        )
        with self.assertRaisesRegex(flow.OAuthError, "access_denied"):
            flow.complete_auth(request)

    def test_signing_in_again_replaces_grant(self):
        for token in ("at-old", "at-new"):
            request = _fake_request(
                session={flow.PENDING_KEY: _pending()},
                get_params={"code": "c", "state": "state-123", "iss": ISSUER},
            )
            with mock.patch.object(flow, "requests") as m:
                m.post.return_value = _resp({**TOKENS, "access_token": token})
                flow.complete_auth(request)
        self.assertEqual(OAuthSession.objects.count(), 1)
        self.assertEqual(OAuthSession.objects.get().access_token, "at-new")


def _saved_session(**overrides):
    fields = {
        "did": "did:plc:alice",
        "handle": "alice.example",
        "pds_url": "https://pds.alice.example",
        "auth_server": ISSUER,
        "token_endpoint": f"{ISSUER}/token",
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "scope": "atproto transition:generic",
        "dpop_jwk": dpop.generate_key(),
    }
    fields.update(overrides)
    return OAuthSession.objects.create(**fields)


@override_settings(MOSAIC_ATPROTO=OAUTH_ON)
class RefreshAndXrpcTest(TestCase):
    def test_refresh_updates_tokens(self):
        session = _saved_session()
        with mock.patch.object(flow, "requests") as m:
            m.post.return_value = _resp(
                {**TOKENS, "access_token": "at-2", "refresh_token": "rt-2"}
            )
            flow.refresh(session)
        body = m.post.call_args[1]["data"]
        self.assertEqual(body["grant_type"], "refresh_token")
        self.assertEqual(body["refresh_token"], "rt-1")
        session.refresh_from_db()
        self.assertEqual(session.access_token, "at-2")
        self.assertEqual(session.refresh_token, "rt-2")

    def test_xrpc_call_sends_dpop_bound_request(self):
        session = _saved_session()
        with mock.patch.object(flow, "requests") as m:
            m.request.return_value = _resp({"records": []})
            data = flow.xrpc_call(
                session,
                "com.atproto.repo.listRecords",
                params={"repo": session.did},
            )
        self.assertEqual(data, {"records": []})
        call = m.request.call_args
        self.assertEqual(call[0][0], "GET")
        self.assertEqual(
            call[0][1],
            "https://pds.alice.example/xrpc/com.atproto.repo.listRecords",
        )
        headers = call[1]["headers"]
        self.assertEqual(headers["Authorization"], "DPoP at-1")
        claims = jwt.decode(headers["DPoP"], options={"verify_signature": False})
        self.assertIn("ath", claims)
        self.assertEqual(claims["htm"], "GET")

    def test_xrpc_nonce_retry_and_persist(self):
        session = _saved_session()
        with mock.patch.object(flow, "requests") as m:
            m.request.side_effect = [
                _resp(
                    {"error": "use_dpop_nonce"},
                    status=401,
                    headers={"DPoP-Nonce": "pds-nonce"},
                ),
                _resp({"ok": True}, headers={"DPoP-Nonce": "pds-nonce"}),
            ]
            data = flow.xrpc_call(session, "com.atproto.repo.getRecord")
        self.assertEqual(data, {"ok": True})
        self.assertEqual(m.request.call_count, 2)
        retry_claims = jwt.decode(
            m.request.call_args_list[1][1]["headers"]["DPoP"],
            options={"verify_signature": False},
        )
        self.assertEqual(retry_claims["nonce"], "pds-nonce")
        session.refresh_from_db()
        self.assertEqual(session.dpop_pds_nonce, "pds-nonce")

    def test_xrpc_refreshes_expired_token_first(self):
        session = _saved_session(
            access_token_expires_at=timezone.now() - timezone.timedelta(minutes=5)
        )
        with (
            mock.patch.object(flow, "refresh") as refresh_mock,
            mock.patch.object(flow, "requests") as m,
        ):
            m.request.return_value = _resp({"ok": True})
            flow.xrpc_call(session, "com.atproto.repo.getRecord")
        refresh_mock.assert_called_once_with(session)

    def test_xrpc_retries_once_after_401_refresh(self):
        session = _saved_session()

        def _refresh(s):
            s.access_token = "at-fresh"
            return s

        with (
            mock.patch.object(flow, "refresh", side_effect=_refresh) as refresh_mock,
            mock.patch.object(flow, "requests") as m,
        ):
            m.request.side_effect = [
                _resp({"error": "invalid_token"}, status=401),
                _resp({"ok": True}),
            ]
            data = flow.xrpc_call(session, "com.atproto.repo.getRecord")
        self.assertEqual(data, {"ok": True})
        refresh_mock.assert_called_once()
        second = m.request.call_args_list[1][1]["headers"]["Authorization"]
        self.assertEqual(second, "DPoP at-fresh")

    def test_xrpc_error_raises(self):
        session = _saved_session(refresh_token="")
        with mock.patch.object(flow, "requests") as m:
            m.request.return_value = _resp({"error": "InvalidRequest"}, status=400)
            with self.assertRaises(flow.OAuthError):
                flow.xrpc_call(session, "com.atproto.repo.getRecord")


@override_settings(MOSAIC_ATPROTO=OAUTH_ON, ROOT_URLCONF=__name__)
class OAuthViewsTest(TestCase):
    def test_client_metadata_served(self):
        resp = self.client.get("/oauth/client-metadata.json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["client_id"],
            "https://client.example/oauth/client-metadata.json",
        )

    def test_jwks_served(self):
        resp = self.client.get("/oauth/jwks.json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["keys"][0]["kid"], "test-key-1")

    def test_login_get_renders_form(self):
        resp = self.client.get("/oauth/login")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="handle"')

    def test_login_post_redirects_to_authorize(self):
        with mock.patch.object(
            oauth_views.flow,
            "start_auth",
            return_value=f"{ISSUER}/authorize?request_uri=urn:x",
        ) as start:
            resp = self.client.post("/oauth/login", {"handle": "@Alice.Example "})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith(f"{ISSUER}/authorize"))
        self.assertEqual(start.call_args[0][1], "alice.example")

    def test_login_post_failure_rerenders_with_error(self):
        with mock.patch.object(
            oauth_views.flow,
            "start_auth",
            side_effect=flow.OAuthError("server unreachable"),
        ):
            resp = self.client.post("/oauth/login", {"handle": "alice.example"})
        self.assertEqual(resp.status_code, 502)
        self.assertContains(resp, "server unreachable", status_code=502)

    def test_login_next_is_validated(self):
        with mock.patch.object(
            oauth_views.flow, "start_auth", return_value=f"{ISSUER}/authorize"
        ):
            self.client.post(
                "/oauth/login",
                {"handle": "alice.example", "next": "https://evil.example/phish"},
            )
        self.assertEqual(self.client.session["mosaic_atproto_oauth_next"], "/")

    def test_callback_signs_in_and_redirects(self):
        saved = _saved_session()
        with mock.patch.object(oauth_views.flow, "complete_auth", return_value=saved):
            resp = self.client.get("/oauth/callback", {"code": "c", "state": "s"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/")

    def test_callback_failure_renders_error(self):
        with mock.patch.object(
            oauth_views.flow,
            "complete_auth",
            side_effect=flow.OAuthError("State mismatch"),
        ):
            resp = self.client.get("/oauth/callback", {"code": "c", "state": "bad"})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "State mismatch", status_code=400)

    def test_logout_clears_session(self):
        session = self.client.session
        session[flow.DID_KEY] = "did:plc:alice"
        session.save()
        resp = self.client.post("/oauth/logout")
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn(flow.DID_KEY, self.client.session)

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get("/oauth/logout").status_code, 405)
