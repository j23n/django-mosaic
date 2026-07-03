from django.http import Http404, HttpResponse
from django.shortcuts import render

from . import conf, lexicons
from .models import PublicationRecord


def lexicon_page(request, page):
    """Render a configured lexicon collection page (e.g. /projects, /books)."""
    config = lexicons.pages().get(page)
    if config is None or not lexicons.read_enabled():
        raise Http404("Unknown collection page.")
    collection = config["collection"]
    records = lexicons.list_records(collection)
    return render(
        request,
        [f"lexicons/pages/{page}.html", "lexicon-page.html"],
        {
            "page": page,
            "title": config.get("title", page.title()),
            "collection": collection,
            "records": records,
            "record_template": [
                f"lexicons/{collection}.html",
                "lexicons/generic.html",
            ],
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
