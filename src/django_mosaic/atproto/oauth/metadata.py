"""OAuth client metadata (the document that *is* our client_id).

ATProto OAuth has no client registration: the client is identified by the
URL of a JSON metadata document it serves, which authorization servers fetch
and validate. Everything here derives from ``OAUTH_CLIENT["BASE_URL"]``.
"""

from .. import conf


def base_url():
    return conf.oauth_client()["BASE_URL"].rstrip("/")


def client_id():
    return f"{base_url()}/oauth/client-metadata.json"


def redirect_uri():
    return f"{base_url()}/oauth/callback"


def jwks_uri():
    return f"{base_url()}/oauth/jwks.json"


def client_metadata():
    """The client metadata document served at /oauth/client-metadata.json."""
    pub = conf.get_setting("PUBLICATION")
    return {
        "client_id": client_id(),
        "client_name": pub.get("NAME") or "mosaic",
        "client_uri": base_url(),
        "application_type": "web",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "redirect_uris": [redirect_uri()],
        "scope": conf.oauth_client()["SCOPE"],
        "dpop_bound_access_tokens": True,
        "token_endpoint_auth_method": "private_key_jwt",
        "token_endpoint_auth_signing_alg": "ES256",
        "jwks_uri": jwks_uri(),
    }
