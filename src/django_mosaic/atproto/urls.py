"""URL patterns for the ATProto bridge.

Include at the project root so the well-known paths land on the domain root::

    urlpatterns = [
        path("", include("django_mosaic.atproto.urls")),
        ...
    ]
"""

from django.urls import path

from . import lexicons
from .views import (
    lexicon_page,
    preview,
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

# One root-level route per configured lexicon page (/projects, /books, ...).
# Include this urlconf before mosaic's namespace catch-all so these win.
urlpatterns += [
    path(slug, lexicon_page, kwargs={"page": slug}, name=f"lexicon-{slug}")
    for slug in lexicons.pages()
]
