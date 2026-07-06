"""ATProto OAuth client (confidential, ``private_key_jwt`` + DPoP).

Requires the ``oauth`` extra: ``pip install django-mosaic[oauth]``. All
modules in this package import ``jwt``/``cryptography`` at module level, so
import this package lazily (the URLconf only wires the routes when
``conf.oauth_enabled()``).

Flow overview (see https://atproto.com/specs/oauth):

1. ``flow.start_auth(request, handle)`` — resolve the handle to its PDS,
   discover the authorization server, push the request (PAR) with PKCE and a
   per-session DPoP key, stash the pending state in the Django session, and
   return the authorize URL to redirect to.
2. ``flow.complete_auth(request)`` — in the callback, exchange the code for
   DPoP-bound tokens and persist an :class:`~..models.OAuthSession`.
3. ``flow.xrpc_call(session, nsid, ...)`` — authenticated XRPC against the
   user's PDS, with automatic DPoP nonces and token refresh.
"""
