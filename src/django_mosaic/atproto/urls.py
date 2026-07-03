"""URL patterns for the ATProto bridge.

Include at the project root so the well-known paths land on the domain root::

    urlpatterns = [
        path("", include("django_mosaic.atproto.urls")),
        ...
    ]
"""

from django.urls import path

from .views import wellknown_atproto_did, wellknown_publication

urlpatterns = [
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
