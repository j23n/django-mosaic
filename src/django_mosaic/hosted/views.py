"""Tenant-site rendering and the claim flow.

The tenant home reuses the atproto preview engine (profile + per-collection
sections read live from the tenant's PDS) — but as the account's *own* site:
indexable, no per-IP throttle. Claiming requires an ATProto OAuth sign-in;
the signed-in DID is the only account a visitor can claim a site for, so
ownership is proven by the OAuth grant rather than anything we store.
"""

import logging

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from django_mosaic.atproto import identity as identity_mod
from django_mosaic.atproto import preview as preview_mod
from django_mosaic.atproto.client import AtprotoError

from . import conf, site_settings
from .models import Tenant, subdomain_validator

logger = logging.getLogger("django_mosaic.hosted")


def _oauth_flow():
    # Lazy so the hosted app imports without the `oauth` extra; only the
    # claim views need it.
    from django_mosaic.atproto.oauth import flow

    return flow


def tenant_home(request):
    """The tenant's site root, rendered live from their PDS."""
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    try:
        identity = identity_mod.resolve(tenant.handle)
    except AtprotoError:
        logger.warning("Could not resolve tenant handle %s", tenant.handle)
        return render(request, "hosted/unavailable.html", status=503)
    profile = preview_mod.fetch_profile(identity)
    built, other_collections = preview_mod.build_sections(identity)
    settings_value = site_settings.load(identity)
    sections = site_settings.arrange(
        built, site_settings.effective_sections(identity, settings_value)
    )
    return render(
        request,
        "hosted/home.html",
        {
            "tenant": tenant,
            "identity": identity,
            "profile": profile,
            "sections": sections,
            "other_collections": other_collections,
            "css_variables": site_settings.css_variables(settings_value),
        },
    )


@require_http_methods(["GET", "POST"])
def claim(request):
    """Claim a subdomain for the signed-in ATProto account."""
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    flow = _oauth_flow()
    session = flow.current_session(request)
    login_url = f"{reverse('atproto-oauth-login')}?next={reverse('hosted-claim')}"
    if session is None:
        if request.method == "POST":
            return redirect(login_url)
        return render(request, "hosted/claim.html", {"login_url": login_url})

    existing = Tenant.objects.filter(did=session.did).first()
    context = {
        "session": session,
        "tenant": existing,
        "base_domain": conf.base_domain(),
        "claim_open": conf.claim_open() and conf.claim_allowed(session.did),
        "suggested": _suggest_subdomain(session.handle),
    }
    if request.method != "POST":
        return render(request, "hosted/claim.html", context)

    if existing:
        return redirect("hosted-claim")
    if not conf.claim_open():
        context["error"] = "New sites are currently closed — join the waitlist."
        return render(request, "hosted/claim.html", context, status=403)
    if not conf.claim_allowed(session.did):
        context["error"] = "This instance does not accept claims for your account."
        return render(request, "hosted/claim.html", context, status=403)

    subdomain = (request.POST.get("subdomain") or "").strip().lower()
    error = _subdomain_error(subdomain)
    if error:
        context.update(error=error, subdomain=subdomain)
        return render(request, "hosted/claim.html", context, status=400)

    tenant = Tenant.objects.create(
        did=session.did, handle=session.handle, subdomain=subdomain
    )
    logger.info("Tenant claimed: %s -> %s", tenant.subdomain, tenant.did)
    context["tenant"] = tenant
    return render(request, "hosted/claim.html", context)


@require_http_methods(["GET", "POST"])
def dashboard(request):
    """Sections + theme for the signed-in tenant, saved to *their* PDS.

    Nothing lands in our database: the POST writes the settings record
    through the tenant's OAuth grant, so the config is as portable as the
    content it arranges.
    """
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    flow = _oauth_flow()
    session = flow.current_session(request)
    if session is None:
        login_url = (
            f"{reverse('atproto-oauth-login')}?next={reverse('hosted-dashboard')}"
        )
        return redirect(login_url)
    tenant = Tenant.objects.filter(did=session.did).first()
    if tenant is None:
        return redirect("hosted-claim")

    try:
        identity = identity_mod.resolve(tenant.handle)
    except AtprotoError:
        return render(request, "hosted/unavailable.html", status=503)
    stored = site_settings.load(identity)
    theme = (stored or {}).get("theme") or {}
    context = {
        "session": session,
        "tenant": tenant,
        "base_domain": conf.base_domain(),
        "sections": site_settings.effective_sections(identity, stored),
        "theme": site_settings.clean_theme(
            theme.get("preset", "plain"), theme.get("tokens")
        ),
        "presets": site_settings.PRESETS,
        "font_choices": site_settings.FONT_CHOICES,
        "radius_choices": site_settings.RADIUS_CHOICES,
        "saved": request.GET.get("saved") == "1",
    }
    if request.method != "POST":
        return render(request, "hosted/dashboard.html", context)

    sections = _parse_sections(request.POST)
    theme = site_settings.clean_theme(
        request.POST.get("preset", "plain"),
        {
            name: request.POST.get(f"token-{name}", "")
            for name in (*site_settings.COLOR_TOKENS, "font", "radius")
        },
    )
    try:
        site_settings.save(session, sections, theme)
    except flow.OAuthError as e:
        logger.warning("Settings save failed for %s: %s", session.did, e)
        context.update(error=str(e), sections=sections, theme=theme)
        return render(request, "hosted/dashboard.html", context, status=502)
    return redirect(f"{reverse('hosted-dashboard')}?saved=1")


def _parse_sections(post):
    """Section config from the dashboard form, ordered by position."""
    rows = []
    for index, collection in enumerate(post.getlist("collection")):
        if not collection or len(collection) > 200:
            continue
        try:
            position = int(post.get(f"position:{collection}", index))
        except (TypeError, ValueError):
            position = index
        rows.append(
            (
                position,
                index,
                {
                    "collection": collection,
                    "title": (post.get(f"title:{collection}") or "").strip()[:100]
                    or collection,
                    "enabled": f"enabled:{collection}" in post,
                },
            )
        )
    return [row[2] for row in sorted(rows, key=lambda r: (r[0], r[1]))]


def _subdomain_error(subdomain):
    from django.core.exceptions import ValidationError

    if not subdomain:
        return "Choose a subdomain."
    try:
        subdomain_validator(subdomain)
    except ValidationError:
        return subdomain_validator.message
    if subdomain in conf.reserved_subdomains():
        return "That subdomain is reserved."
    if Tenant.objects.filter(subdomain=subdomain).exists():
        return "That subdomain is taken."
    return None


def _suggest_subdomain(handle):
    """A claimable suggestion from the handle's first label."""
    label = handle.split(".", 1)[0].lower()
    cleaned = "".join(c for c in label if c.isalnum() or c == "-").strip("-")[:63]
    if not cleaned or _subdomain_error(cleaned):
        return ""
    return cleaned
