"""Tenant-site rendering and the claim flow.

The tenant home reuses the atproto preview engine (profile + per-collection
sections read live from the tenant's PDS) — but as the account's *own* site:
indexable, no per-IP throttle. Claiming requires an ATProto OAuth sign-in;
the signed-in DID is the only account a visitor can claim a site for, so
ownership is proven by the OAuth grant rather than anything we store.
"""

import logging

import requests
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from django_mosaic.atproto import identity as identity_mod
from django_mosaic.atproto import preview as preview_mod
from django_mosaic.atproto.client import AtprotoError

from . import composer, conf, site_settings
from .models import Report, Tenant, domain_validator, subdomain_validator

logger = logging.getLogger("django_mosaic.hosted")


def _oauth_flow():
    # Lazy so the hosted app imports without the `oauth` extra; only the
    # claim views need it.
    from django_mosaic.atproto.oauth import flow

    return flow


def _tenant_identity(tenant):
    """Resolve a tenant's Identity from its immutable DID.

    Ownership was proven on the DID at claim time, so we resolve the DID —
    never the (mutable) handle — to a PDS. If the handle was later taken over
    by another account, that account's DID differs and cannot hijack this
    tenant's site.
    """
    return identity_mod.resolve_did(tenant.did, handle=tenant.handle)


def tenant_home(request):
    """The tenant's site root, rendered live from their PDS."""
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    try:
        identity = _tenant_identity(tenant)
    except AtprotoError:
        logger.warning("Could not resolve tenant DID %s", tenant.did)
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
            "has_custom_css": bool(site_settings.custom_css(settings_value)),
            # Consumed by the site.standard.document partial: only tenant
            # hosts have /posts/<rkey> pages to link to.
            "documents_linked": True,
            # Reversed against the base domain by hand — this request runs
            # under the tenant urlconf, where hosted-report doesn't exist.
            "report_url": f"//{conf.base_domain()}/report?site={tenant.subdomain}",
        },
    )


def tenant_document(request, rkey):
    """A single published document on the tenant's site (/posts/<rkey>)."""
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    # Reject non-TID rkeys before they reach a cache key or a PDS round-trip.
    if not composer.is_valid_tid(rkey):
        raise Http404("No such post.")
    try:
        identity = _tenant_identity(tenant)
    except AtprotoError:
        return render(request, "hosted/unavailable.html", status=503)
    value = composer.get_document(identity, rkey)
    if value is None:
        raise Http404("No such post.")
    settings_value = site_settings.load(identity)
    return render(
        request,
        "hosted/document.html",
        {
            "tenant": tenant,
            "identity": identity,
            "document": value,
            "markdown_source": composer.document_markdown(value),
            "css_variables": site_settings.css_variables(settings_value),
            "has_custom_css": bool(site_settings.custom_css(settings_value)),
        },
    )


def tenant_custom_css(request):
    """The tenant's custom stylesheet (/custom.css), served standalone.

    Kept out of the HTML on purpose: a stylesheet response can't inject
    markup, and it only ever styles the tenant's own site.
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404("Not a tenant host.")
    try:
        identity = _tenant_identity(tenant)
    except AtprotoError:
        return HttpResponse("", content_type="text/css", status=503)
    css = site_settings.custom_css(site_settings.load(identity))
    response = HttpResponse(css, content_type="text/css")
    response["X-Content-Type-Options"] = "nosniff"
    return response


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

    Returns 200 only for hosts we will actually serve — the base domain
    itself, active tenants' subdomains, and registered custom domains — so
    the server never requests certificates for anything else. Approving
    subdomains here means an ingress with on-demand TLS needs no wildcard
    certificate (and thus no DNS-challenge plumbing). Point Caddy's
    `on_demand_tls { ask }` at this URL.
    """
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    domain = normalize_domain(request.GET.get("domain") or "")
    if domain and _serves_domain(domain):
        return HttpResponse("ok", content_type="text/plain")
    return HttpResponse("unknown domain", content_type="text/plain", status=404)


def _serves_domain(domain):
    """Whether this host is one mosaic serves (mirrors TenantMiddleware)."""
    base = conf.base_domain()
    if domain == base:
        return True
    if domain.endswith("." + base):
        label = domain[: -len(base) - 1]
        if "." in label:  # nested subdomains are never tenant hosts
            return False
        return Tenant.objects.filter(
            subdomain=label, status=Tenant.STATUS_ACTIVE
        ).exists()
    return Tenant.objects.filter(
        custom_domain=domain, status=Tenant.STATUS_ACTIVE
    ).exists()


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

    try:
        tenant = Tenant.objects.create(
            did=session.did, handle=session.handle, subdomain=subdomain
        )
    except IntegrityError:
        # Lost a concurrent race for the same subdomain (unique constraint).
        context.update(error="That subdomain was just taken.", subdomain=subdomain)
        return render(request, "hosted/claim.html", context, status=409)
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
        identity = _tenant_identity(tenant)
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
        "custom_css": site_settings.custom_css(stored),
        "custom_css_max": site_settings.CUSTOM_CSS_MAX,
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
    custom_css = request.POST.get("custom_css", "")
    if len(custom_css) > site_settings.CUSTOM_CSS_MAX:
        # Reject rather than silently truncate mid-rule.
        context.update(
            error=(
                f"Custom CSS is limited to {site_settings.CUSTOM_CSS_MAX} "
                "characters."
            ),
            sections=sections,
            theme=theme,
            custom_css=custom_css,
        )
        return render(request, "hosted/dashboard.html", context, status=400)
    try:
        site_settings.save(session, sections, theme, custom_css=custom_css)
    except (flow.OAuthError, requests.RequestException) as e:
        logger.warning("Settings save failed for %s: %s", session.did, e)
        context.update(
            error=str(e), sections=sections, theme=theme, custom_css=custom_css
        )
        return render(request, "hosted/dashboard.html", context, status=502)
    return redirect(f"{reverse('hosted-dashboard')}?saved=1")


@require_http_methods(["GET", "POST"])
def dashboard_write(request):
    """The composer: publish a standard.site document to the tenant's PDS."""
    if not conf.enabled():
        raise Http404("Hosting is not enabled.")
    flow = _oauth_flow()
    session = flow.current_session(request)
    if session is None:
        return redirect(
            f"{reverse('atproto-oauth-login')}?next={reverse('hosted-write')}"
        )
    tenant = Tenant.objects.filter(did=session.did).first()
    if tenant is None:
        return redirect("hosted-claim")

    context = {
        "session": session,
        "tenant": tenant,
        "base_domain": conf.base_domain(),
        "body_max_kb": composer.BODY_MAX_BYTES // 1000,
    }
    if request.method != "POST":
        return render(request, "hosted/write.html", context)

    title = request.POST.get("title", "")
    description = request.POST.get("description", "")
    body = request.POST.get("body", "")
    try:
        published = composer.publish(session, tenant, title, body, description)
    except composer.ComposerError as e:
        context.update(error=str(e), title=title, description=description, body=body)
        return render(request, "hosted/write.html", context, status=400)
    context["published"] = published
    return render(request, "hosted/write.html", context)


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

    domain = normalize_domain(request.POST.get("domain") or "")
    error = _domain_error(domain)
    if not error:
        error = _register_domain(tenant, domain)
    if error:
        request.session["mosaic_hosted_domain_error"] = error
        return redirect("hosted-dashboard")
    logger.info("Custom domain registered: %s -> %s", domain, tenant.did)
    return redirect("hosted-dashboard")


def normalize_domain(value):
    """Canonical form of a hostname for storage and comparison."""
    return value.strip().lower().strip(".")


def _register_domain(tenant, domain):
    """Assign `domain` to `tenant`, reclaiming it if only unverified. Returns
    an error string, or None on success.

    A *verified* domain is locked to its tenant (proven control), so no one
    can take it. An unverified registration is just a pending intent and can
    be reclaimed — this stops a squatter from permanently blocking the real
    owner by registering a string they don't control. Whoever's DNS actually
    points here verifies on first request and locks it.
    """
    with transaction.atomic():
        holder = (
            Tenant.objects.select_for_update()
            .exclude(pk=tenant.pk)
            .filter(custom_domain=domain)
            .first()
        )
        if holder is not None:
            if holder.domain_verified_at is not None:
                return "That domain is already connected to another site."
            holder.custom_domain = None
            holder.domain_verified_at = None
            holder.save(update_fields=["custom_domain", "domain_verified_at"])
        locked = Tenant.objects.select_for_update().get(pk=tenant.pk)
        locked.custom_domain = domain
        locked.domain_verified_at = None  # re-verified on first request
        locked.save(update_fields=["custom_domain", "domain_verified_at"])
    return None


def _domain_error(domain):
    if not domain:
        return "Enter a domain."
    try:
        domain_validator(domain)
    except ValidationError:
        return domain_validator.message
    base = conf.base_domain()
    if domain == base or domain.endswith("." + base):
        return f"Domains under {base} are assigned via your subdomain."
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
    per (IP, site). Reports land in the admin, where the Tenant suspend
    action is one click away.

    The throttle keys on REMOTE_ADDR: behind a reverse proxy, configure
    real-IP forwarding (e.g. nginx real_ip) or every visitor shares one
    bucket. Keying additionally on the reported site means even a collapsed
    IP can only throttle reports against a single tenant, never globally.
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
    throttle_key = f"mosaic_hosted:report_rate:{ip}:{tenant.pk}"
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
