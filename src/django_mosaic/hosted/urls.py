"""Service-domain routes for the hosted app (the claim flow).

Include on the base domain alongside the atproto URLs (which provide
/oauth/* and, with PREVIEW_LANDING, the landing page)::

    urlpatterns = [
        path("", include("django_mosaic.atproto.urls")),
        path("", include("django_mosaic.hosted.urls")),
        ...
    ]
"""

from django.urls import path

from .views import claim, dashboard

urlpatterns = [
    path("claim", claim, name="hosted-claim"),
    path("dashboard", dashboard, name="hosted-dashboard"),
]
