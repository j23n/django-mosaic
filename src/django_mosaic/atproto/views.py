from django.http import Http404, HttpResponse
from django.shortcuts import render

from . import conf, lexicons
from . import identity as identity_mod
from . import preview as preview_mod
from .client import AtprotoError
from .models import PublicationRecord


def lexicon_page(request, page):
    """Render a configured lexicon collection page (e.g. /projects, /books)."""
    config = lexicons.pages().get(page)
    if config is None or not lexicons.read_enabled():
        raise Http404("Unknown collection page.")
    collection = config["collection"]
    identity = lexicons.owner_identity()
    records = lexicons.list_records(collection, identity=identity)
    return render(
        request,
        [f"lexicons/pages/{page}.html", "lexicon-page.html"],
        {
            "page": page,
            "title": config.get("title", page.title()),
            "collection": collection,
            "identity": identity,
            "records": records,
            "record_template": [
                f"lexicons/{collection}.html",
                "lexicons/generic.html",
            ],
        },
    )


def preview(request, handle):
    """Read-only preview of any handle's ATmosphere content (/@handle).

    Opt-in via MOSAIC_ATPROTO["PREVIEW"]; renders only public repo data.
    """
    if not preview_mod.enabled():
        raise Http404("Preview mode is disabled.")
    handle = handle.strip().lstrip("@").lower()
    try:
        identity = identity_mod.resolve(handle)
    except AtprotoError as e:
        raise Http404(f"Could not resolve handle: {handle}") from e
    profile = preview_mod.fetch_profile(identity)
    sections, other_collections = preview_mod.build_sections(identity)
    return render(
        request,
        "atproto/preview.html",
        {
            "identity": identity,
            "profile": profile,
            "sections": sections,
            "other_collections": other_collections,
        },
    )


def wellknown_atproto_did(request):
    """Serve /.well-known/atproto-did so this domain can be the handle."""
    did = conf.get_setting("DID")
    if not did:
        raise Http404("No DID configured.")
    return HttpResponse(did, content_type="text/plain")


def wellknown_publication(request):
    """Serve /.well-known/site.standard.publication (domain verification)."""
    publication = PublicationRecord.objects.first()
    if not publication:
        raise Http404("No publication record yet.")
    return HttpResponse(publication.uri, content_type="text/plain")
