"""Views for the ATProto OAuth client.

Mounted under ``/oauth/`` by the bridge URLconf when ``conf.oauth_enabled()``.
``client-metadata.json`` and ``jwks.json`` are the public documents
authorization servers fetch to validate this client; login/callback/logout
drive the visitor-facing flow.
"""

import logging

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..client import AtprotoError
from . import flow, keys, metadata

logger = logging.getLogger("django_mosaic.atproto")


def client_metadata(request):
    return JsonResponse(metadata.client_metadata())


def jwks(request):
    return JsonResponse(keys.client_jwks())


def _safe_next(request, value):
    if value and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return "/"


def login(request):
    """GET renders the handle form; POST starts the authorization flow."""
    if request.method != "POST":
        return render(
            request,
            "atproto/oauth-login.html",
            {"session": flow.current_session(request)},
        )
    handle = (request.POST.get("handle") or "").strip().lstrip("@").lower()
    if not handle:
        return HttpResponseBadRequest("A handle is required.")
    request.session["mosaic_atproto_oauth_next"] = _safe_next(
        request, request.POST.get("next")
    )
    try:
        return redirect(flow.start_auth(request, handle))
    except (flow.OAuthError, AtprotoError) as e:
        logger.warning("OAuth start failed for %s: %s", handle, e)
        return render(
            request,
            "atproto/oauth-login.html",
            {"error": str(e), "handle": handle},
            status=502,
        )


def callback(request):
    """The redirect_uri: finish the exchange and sign the visitor in."""
    try:
        flow.complete_auth(request)
    except flow.OAuthError as e:
        logger.warning("OAuth callback failed: %s", e)
        return render(
            request, "atproto/oauth-login.html", {"error": str(e)}, status=400
        )
    next_url = request.session.pop("mosaic_atproto_oauth_next", "/")
    return redirect(_safe_next(request, next_url))


@require_POST
def logout(request):
    flow.logout(request)
    return redirect("/")
