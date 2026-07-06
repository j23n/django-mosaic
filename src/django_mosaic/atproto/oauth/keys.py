"""Client signing key (ES256) and JWK helpers.

The confidential client authenticates to authorization servers with
``private_key_jwt``: a long-lived ES256 key whose public half is published at
``/oauth/jwks.json``. DPoP keys are different — short-lived, one per user
session — but share the JWK plumbing here.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .. import conf


class OAuthConfigError(Exception):
    """Raised when the OAUTH_CLIENT settings are missing or malformed."""


def generate_private_key_pem():
    """A fresh ES256 (P-256) private key, PEM-encoded (for `atproto oauth-key`)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def load_client_key():
    """The configured client private key as a cryptography EC key object."""
    pem = conf.oauth_client()["PRIVATE_KEY"]
    if not pem:
        raise OAuthConfigError(
            "MOSAIC_ATPROTO['OAUTH_CLIENT']['PRIVATE_KEY'] is not set; "
            "generate one with `manage.py atproto oauth-key`."
        )
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise OAuthConfigError("OAUTH_CLIENT PRIVATE_KEY must be an ES256 (P-256) key")
    return key


def _b64url_uint(value, length=32):
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def public_jwk(key, kid=None):
    """The public JWK dict for an EC P-256 key object."""
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url_uint(numbers.x),
        "y": _b64url_uint(numbers.y),
        "use": "sig",
        "alg": "ES256",
    }
    if kid:
        jwk["kid"] = kid
    return jwk


def client_jwks():
    """The JWKS document published at /oauth/jwks.json."""
    key = load_client_key()
    kid = conf.oauth_client()["KEY_ID"]
    return {"keys": [public_jwk(key, kid=kid)]}
