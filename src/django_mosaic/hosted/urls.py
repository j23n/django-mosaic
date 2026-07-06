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

from .views import claim, dashboard, dashboard_domain, domain_check, report

urlpatterns = [
    path("claim", claim, name="hosted-claim"),
    path("dashboard", dashboard, name="hosted-dashboard"),
    path("dashboard/domain", dashboard_domain, name="hosted-dashboard-domain"),
    # On-demand TLS `ask` endpoint — point Caddy's on_demand_tls at this.
    path("domains/check", domain_check, name="hosted-domain-check"),
    path("report", report, name="hosted-report"),
]
