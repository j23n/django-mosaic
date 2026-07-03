from django.http import Http404, HttpResponse

from . import conf
from .models import PublicationRecord


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
