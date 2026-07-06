"""Tenant-site rendering and the claim flow.

The tenant home reuses the atproto preview engine (profile + per-collection
sections read live from the tenant's PDS) — but as the account's *own* site:
indexable, no per-IP throttle. Claiming requires an ATProto OAuth sign-in;
the signed-in DID is the only account a visitor can claim a site for, so
ownership is proven by the OAuth grant rather than anything we store.
"""

import logging

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from django_mosaic.atproto import identity as identity_mod
from django_mosaic.atproto import preview as preview_mod
from django_mosaic.atproto.client import AtprotoError

from . import conf, site_settings
from .models import Report, Tenant, domain_validator, subdomain_validator

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
            # Reversed against the base domain by hand — this request runs
            # under the tenant urlconf, where hosted-report doesn't exist.
            "report_url": f"//{conf.base_domain()}/report?site={tenant.subdomain}",
        },
    )


def tenant_wellknown_did(request):
    """Serve /.well-known/atproto-did on tenant hosts (domain-as-handle).

    On a verified custom domain this lets the tenant switch their ATProto
    handle to that domain ("No DNS panel" flow in Bluesky settings) — we
    answer the handle-verification fetch with their DID.
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    return HttpResponse(tenant.did, content_type="text/plain")


def domain_check(request):
    """The on-demand TLS `ask` endpoint (Caddy/others) on the base domain.

    Returns 200 only for domains an active tenant has registered, so the
    server never requests certificates for hosts we won't serve. Point
    Caddy's `on_demand_tls { ask }` at this URL.
    """
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    domain = (request.GET.get("domain") or "").lower().strip(".")
    if (
        domain
        and Tenant.objects.filter(
            custom_domain=domain, status=Tenant.STATUS_ACTIVE
        ).exists()
    ):
        return HttpResponse("ok", content_type="text/plain")
    return HttpResponse("unknown domain", content_type="text/plain", status=404)


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
        "domain_target": conf.domain_target(),
        "domain_error": request.session.pop("mosaic_hosted_domain_error", ""),
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


@require_POST
def dashboard_domain(request):
    """Set or remove the signed-in tenant's custom domain."""
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    session = _oauth_flow().current_session(request)
    tenant = (
        Tenant.objects.filter(did=session.did).first() if session is not None else None
    )
    if tenant is None:
        return redirect("hosted-claim")

    if request.POST.get("remove"):
        tenant.custom_domain = None
        tenant.domain_verified_at = None
        tenant.save(update_fields=["custom_domain", "domain_verified_at"])
        return redirect("hosted-dashboard")

    domain = (request.POST.get("domain") or "").lower().strip().strip(".")
    error = _domain_error(domain, tenant)
    if error:
        request.session["mosaic_hosted_domain_error"] = error
        return redirect("hosted-dashboard")
    tenant.custom_domain = domain
    tenant.domain_verified_at = None  # re-verified on first request
    tenant.save(update_fields=["custom_domain", "domain_verified_at"])
    logger.info("Custom domain registered: %s -> %s", domain, tenant.did)
    return redirect("hosted-dashboard")


def _domain_error(domain, tenant):
    if not domain:
        return "Enter a domain."
    try:
        domain_validator(domain)
    except ValidationError:
        return domain_validator.message
    base = conf.base_domain()
    if domain == base or domain.endswith("." + base):
        return f"Domains under {base} are assigned via your subdomain."
    if Tenant.objects.exclude(pk=tenant.pk).filter(custom_domain=domain).exists():
        return "That domain is already registered to another site."
    return None


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


REPORTS_PER_HOUR = 5


@require_http_methods(["GET", "POST"])
def report(request):
    """File an abuse report against a tenant site (?site=<subdomain|domain>).

    Anonymous by design (contact optional); honeypot-filtered and throttled
    per IP. Reports land in the admin, where the Tenant suspend action is
    one click away.
    """
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    site = (request.GET.get("site") or request.POST.get("site") or "").lower().strip()
    tenant = Tenant.objects.filter(subdomain=site).first() or (
        Tenant.objects.filter(custom_domain=site).first()
    )
    context = {"site": site, "tenant": tenant, "base_domain": conf.base_domain()}
    if request.method != "POST":
        return render(request, "hosted/report.html", context)

    if request.POST.get("website"):  # honeypot
        return render(request, "hosted/report.html", {**context, "submitted": True})
    if tenant is None:
        context["error"] = "Unknown site — pass the subdomain or domain to report."
        return render(request, "hosted/report.html", context, status=400)
    reason = (request.POST.get("reason") or "").strip()[:2000]
    if not reason:
        context["error"] = "Describe the problem."
        return render(request, "hosted/report.html", context, status=400)

    ip = request.META.get("REMOTE_ADDR", "")
    throttle_key = f"mosaic_hosted:report_rate:{ip}"
    if not cache.add(throttle_key, 1, timeout=3600):
        try:
            count = cache.incr(throttle_key)
        except ValueError:
            count = 1
        if count > REPORTS_PER_HOUR:
            # Pretend success: don't hand abusers a signal to tune against.
            return render(request, "hosted/report.html", {**context, "submitted": True})

    Report.objects.create(
        tenant=tenant,
        reason=reason,
        reporter_contact=(request.POST.get("contact") or "").strip()[:320],
    )
    logger.info("Report filed against tenant %s", tenant.subdomain)
    return render(request, "hosted/report.html", {**context, "submitted": True})
