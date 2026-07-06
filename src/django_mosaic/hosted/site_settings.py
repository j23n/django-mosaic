"""Per-tenant site settings, stored in the *tenant's own* PDS.

The whole configuration — section list and theme — lives as a single record
(``blog.mosaic.site.settings``, rkey ``self``) in the tenant's repo, not in
our database: no lock-in by construction (any mosaic instance pointed at the
handle reproduces the site), and the hosted service stays near-stateless.

Reads are unauthenticated public XRPC (cached); writes go through the
tenant's OAuth session. Theme customization is deliberately a fixed set of
validated design tokens — never raw CSS — so a record written by anyone
(it's the user's repo, we don't control it) cannot inject style or markup.
"""

import logging
import re

import requests
from django.core.cache import cache
from django.utils import timezone

from django_mosaic.atproto import lexicons
from django_mosaic.atproto.client import AtprotoError, xrpc_get

logger = logging.getLogger("django_mosaic.hosted")

SETTINGS_NSID = "blog.mosaic.site.settings"
SETTINGS_RKEY = "self"
CACHE_SECONDS = 300

# The custom-CSS escape hatch (the Tumblr/Bearblog model). Size-capped, and
# served as a standalone text/css response on the tenant's own host — never
# inlined into HTML — so it cannot inject markup. It styles only the
# tenant's own site.
CUSTOM_CSS_MAX = 20_000

# The theme-token vocabulary: every value is validated against these before
# it gets anywhere near a stylesheet. Enum tokens map to CSS in the template.
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
COLOR_TOKENS = ("accent", "background", "text")
FONT_CHOICES = {
    "sans": '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    "serif": 'Georgia, "Times New Roman", serif',
    "mono": 'ui-monospace, "Cascadia Code", Menlo, Consolas, monospace',
}
RADIUS_CHOICES = {"none": "0", "small": "4px", "large": "12px"}

# Curated starting points; a preset is just a token bundle.
PRESETS = {
    "plain": {},
    "paper": {
        "accent": "#8b3a2f",
        "background": "#f7f3e9",
        "text": "#2b2620",
        "font": "serif",
        "radius": "small",
    },
    "night": {
        "accent": "#7cc4ff",
        "background": "#14161a",
        "text": "#e6e8eb",
        "font": "sans",
        "radius": "large",
    },
}


def _cache_key(did):
    return f"mosaic_hosted:settings:{did}"


def load(identity):
    """The tenant's settings record value, or None (missing/unreachable)."""
    cached = cache.get(_cache_key(identity.did))
    if cached is not None:
        return cached or None  # "" marks a cached miss
    try:
        data = xrpc_get(
            identity.pds_url,
            "com.atproto.repo.getRecord",
            {
                "repo": identity.did,
                "collection": SETTINGS_NSID,
                "rkey": SETTINGS_RKEY,
            },
        )
        value = data.get("value") or None
    except (AtprotoError, requests.RequestException):
        value = None  # not written yet (or PDS unreachable) — defaults apply
    cache.set(_cache_key(identity.did), value or "", CACHE_SECONDS)
    return value


def save(oauth_session, sections, theme, custom_css=""):
    """Write the settings record to the tenant's repo via their OAuth grant."""
    from django_mosaic.atproto.oauth import flow

    record = {
        "$type": SETTINGS_NSID,
        "sections": sections,
        "theme": theme,
        "updatedAt": timezone.now().isoformat(timespec="seconds"),
    }
    custom_css = (custom_css or "")[:CUSTOM_CSS_MAX]
    if custom_css.strip():
        record["customCss"] = custom_css
    flow.xrpc_call(
        oauth_session,
        "com.atproto.repo.putRecord",
        method="POST",
        json_body={
            "repo": oauth_session.did,
            "collection": SETTINGS_NSID,
            "rkey": SETTINGS_RKEY,
            "record": record,
        },
    )
    cache.delete(_cache_key(oauth_session.did))
    return record


def clean_theme(preset, tokens):
    """Validated theme dict from untrusted input; unknown/invalid dropped."""
    theme = {"preset": preset if preset in PRESETS else "plain", "tokens": {}}
    supplied = {k: v for k, v in (tokens or {}).items() if v}
    merged = {**PRESETS[theme["preset"]], **supplied}
    for name in COLOR_TOKENS:
        value = str(merged.get(name, "")).strip()
        if COLOR_RE.match(value):
            theme["tokens"][name] = value
    if merged.get("font") in FONT_CHOICES:
        theme["tokens"]["font"] = merged["font"]
    if merged.get("radius") in RADIUS_CHOICES:
        theme["tokens"]["radius"] = merged["radius"]
    return theme


def css_variables(settings_value):
    """A `--mosaic-*` custom-property block from a (re-validated) record.

    The record comes from the user's repo, so validation happens on the read
    path too — never trust stored data just because we wrote it once.
    """
    theme = (settings_value or {}).get("theme") or {}
    tokens = clean_theme(theme.get("preset", "plain"), theme.get("tokens"))["tokens"]
    css = {}
    for name in COLOR_TOKENS:
        if name in tokens:
            css[f"--mosaic-{name}"] = tokens[name]
    if "font" in tokens:
        css["--mosaic-font"] = FONT_CHOICES[tokens["font"]]
    if "radius" in tokens:
        css["--mosaic-radius"] = RADIUS_CHOICES[tokens["radius"]]
    return "".join(f"{k}:{v};" for k, v in css.items())


def custom_css(settings_value):
    """The tenant's custom stylesheet text, size-capped ('' when unset)."""
    value = (settings_value or {}).get("customCss")
    if not isinstance(value, str):
        return ""
    return value[:CUSTOM_CSS_MAX]


def default_sections(identity):
    """The starting section list: known collections present in the repo."""
    try:
        present = lexicons.describe_repo(identity)
    except Exception:  # noqa: BLE001 - dashboard must render regardless
        present = []
    return [
        {"collection": collection, "title": title, "enabled": True}
        for collection, title in lexicons.PREVIEW_COLLECTIONS.items()
        if collection in present
    ]


def effective_sections(identity, settings_value):
    """Merged section config: stored order/titles/toggles, then any known
    collections that appeared in the repo since the record was written."""
    configured = []
    seen = set()
    for entry in (settings_value or {}).get("sections") or []:
        collection = entry.get("collection")
        if not isinstance(collection, str) or collection in seen:
            continue
        seen.add(collection)
        configured.append(
            {
                "collection": collection,
                "title": str(entry.get("title") or "")[:100]
                or lexicons.PREVIEW_COLLECTIONS.get(collection, collection),
                "enabled": bool(entry.get("enabled", True)),
            }
        )
    for entry in default_sections(identity):
        if entry["collection"] not in seen:
            configured.append(entry)
    return configured


def arrange(built_sections, section_config):
    """Order/filter/retitle preview-built sections per the tenant's config."""
    by_collection = {s["collection"]: s for s in built_sections}
    arranged = []
    placed = set()
    for entry in section_config:
        section = by_collection.get(entry["collection"])
        if section is None:
            continue
        placed.add(entry["collection"])
        if entry["enabled"]:
            arranged.append({**section, "title": entry["title"]})
    arranged += [s for s in built_sections if s["collection"] not in placed]
    return arranged
