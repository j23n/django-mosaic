"""DPoP proof JWTs (RFC 9449, as profiled by ATProto OAuth).

Every token request and every authenticated XRPC call carries a ``DPoP``
header: a short-lived JWT signed with a per-user-session ES256 key, binding
the request method/URL (and, for resource calls, the access token hash) to
that key. Servers hand out nonces via the ``DPoP-Nonce`` response header and
reject proofs without one using the ``use_dpop_nonce`` error — callers retry
once with the nonce (see ``flow._request_with_dpop``).
"""

import base64
import hashlib
import secrets
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .keys import public_jwk


def generate_key():
    """A fresh per-session DPoP keypair, serialized as a private JWK dict."""
    key = ec.generate_private_key(ec.SECP256R1())
    private_value = key.private_numbers().private_value
    jwk = public_jwk(key)
    jwk.pop("use", None)
    jwk.pop("alg", None)
    jwk["d"] = (
        base64.urlsafe_b64encode(private_value.to_bytes(32, "big"))
        .rstrip(b"=")
        .decode()
    )
    return jwk


def _load_private_jwk(jwk):
    def _int(name):
        return int.from_bytes(
            base64.urlsafe_b64decode(jwk[name] + "=" * (-len(jwk[name]) % 4)), "big"
        )

    public = ec.EllipticCurvePublicNumbers(_int("x"), _int("y"), ec.SECP256R1())
    return ec.EllipticCurvePrivateNumbers(_int("d"), public).private_key()


def public_part(jwk):
    return {k: jwk[k] for k in ("kty", "crv", "x", "y")}


def proof(jwk, method, url, nonce=None, access_token=None):
    """A signed DPoP proof JWT for one HTTP request."""
    key = _load_private_jwk(jwk)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    claims = {
        "jti": secrets.token_urlsafe(16),
        "htm": method.upper(),
        "htu": url.split("#", 1)[0].split("?", 1)[0],
        "iat": int(time.time()),
    }
    if nonce:
        claims["nonce"] = nonce
    if access_token:
        digest = hashlib.sha256(access_token.encode()).digest()
        claims["ath"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return jwt.encode(
        claims,
        pem,
        algorithm="ES256",
        headers={"typ": "dpop+jwt", "jwk": public_part(jwk)},
    )
