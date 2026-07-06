"""URL patterns for the ATProto bridge.

Include at the project root so the well-known paths land on the domain root::

    urlpatterns = [
        path("", include("django_mosaic.atproto.urls")),
        ...
    ]
"""

from django.core.exceptions import ImproperlyConfigured
from django.urls import path

from . import conf, lexicons
from . import preview as preview_mod
from .views import (
    lexicon_page,
    preview,
    preview_landing,
    waitlist_signup,
    wellknown_atproto_did,
    wellknown_publication,
)

urlpatterns = [
    # Read-only preview of any handle (opt-in via MOSAIC_ATPROTO["PREVIEW"]).
    path("@<str:handle>", preview, name="atproto-preview"),
    path(
        ".well-known/atproto-did",
        wellknown_atproto_did,
        name="atproto-did",
    ),
    path(
        ".well-known/site.standard.publication",
        wellknown_publication,
        name="atproto-publication",
    ),
]

# Dedicated preview-service mode: the landing page takes over the site root
# (this urlconf is included before mosaic's, so it wins when enabled). Routes
# are built at import time from settings, like the lexicon pages below.
if preview_mod.landing_enabled():
    urlpatterns += [
        path("", preview_landing, name="atproto-preview-landing"),
        path("preview/waitlist", waitlist_signup, name="atproto-waitlist"),
    ]

# ATProto OAuth client (sign in with any ATProto account). Routes appear only
# when OAUTH_CLIENT is configured; the modules need the `oauth` extra.
if conf.oauth_enabled():
    try:
        from .oauth import views as oauth_views
    except ImportError as exc:
        raise ImproperlyConfigured(
            "MOSAIC_ATPROTO['OAUTH_CLIENT'] is configured but the OAuth "
            "dependencies are missing — install django-mosaic[oauth]."
        ) from exc
    urlpatterns += [
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

# One root-level route per configured lexicon page (/projects, /books, ...).
# Include this urlconf before mosaic's namespace catch-all so these win.
urlpatterns += [
    path(slug, lexicon_page, kwargs={"page": slug}, name=f"lexicon-{slug}")
    for slug in lexicons.pages()
]
