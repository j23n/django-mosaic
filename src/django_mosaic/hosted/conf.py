"""Settings access for the hosted (multi-tenant) app.

Everything lives under a single ``MOSAIC_HOSTED`` dict, mirroring
``MOSAIC_ATPROTO``. The app is inert until ``BASE_DOMAIN`` is set.
"""

from django.conf import settings

DEFAULTS = {
    # The apex domain tenants get subdomains of (e.g. "mosaic.example" →
    # alice.mosaic.example). Required; the app is inert without it. Requests
    # whose Host is exactly this domain (or not under it at all) fall through
    # to the normal URLconf.
    "BASE_DOMAIN": "",
    # Subdomains that can never be claimed. Merged with a built-in list of
    # infrastructure names.
    "RESERVED_SUBDOMAINS": [],
    # Whether new tenants may claim subdomains. Turn off to freeze signups
    # while keeping existing tenants served.
    "CLAIM_OPEN": True,
    # DIDs that may claim a site; empty means anyone (subject to CLAIM_OPEN).
    # Lets a self-hoster pin claiming to their own DID from first boot instead
    # of claiming and then flipping CLAIM_OPEN off.
    "CLAIM_ALLOWED_DIDS": [],
    # The DNS target shown in the custom-domain instructions (what tenants
    # point their CNAME/ALIAS at). Defaults to the base domain.
    "DOMAIN_TARGET": "",
    # A pending (unverified) custom-domain registration is reclaimable by a
    # different tenant only after this many hours without verification. The
    # window must comfortably exceed DNS propagation + the first request, so a
    # real owner who has pointed DNS verifies and locks the domain long before
    # anyone can reclaim it — while a stale squat of a string nobody controls
    # eventually frees up. Guards the domain-hijack race in `_register_domain`.
    "DOMAIN_RECLAIM_HOURS": 72,
}

# Always reserved regardless of settings — infrastructure and confusables.
BUILTIN_RESERVED = {
    "www",
    "mail",
    "smtp",
    "imap",
    "ns1",
    "ns2",
    "api",
    "admin",
    "static",
    "media",
    "cdn",
    "assets",
    "blog",
    "docs",
    "help",
    "support",
    "status",
    "billing",
    "dashboard",
    "app",
    "oauth",
    "auth",
    "login",
    "preview",
    "staging",
    "test",
    "dev",
    "mosaic",
    "official",
}


def get_setting(name):
    conf = getattr(settings, "MOSAIC_HOSTED", {})
    return conf.get(name, DEFAULTS[name])


def as_dict():
    return {**DEFAULTS, **getattr(settings, "MOSAIC_HOSTED", {})}


def enabled():
    return bool(get_setting("BASE_DOMAIN"))


def base_domain():
    return get_setting("BASE_DOMAIN").lower().strip(".")


def reserved_subdomains():
    return BUILTIN_RESERVED | {s.lower() for s in get_setting("RESERVED_SUBDOMAINS")}


def claim_open():
    return enabled() and bool(get_setting("CLAIM_OPEN"))


def claim_allowed(did):
    """Whether this DID may claim a site (CLAIM_ALLOWED_DIDS empty = anyone)."""
    allowed = get_setting("CLAIM_ALLOWED_DIDS")
    return not allowed or did in allowed


def domain_target():
    return get_setting("DOMAIN_TARGET") or base_domain()


def domain_reclaim_after():
    """How long a pending domain registration is protected from reclaim."""
    from datetime import timedelta

    return timedelta(hours=get_setting("DOMAIN_RECLAIM_HOURS"))
