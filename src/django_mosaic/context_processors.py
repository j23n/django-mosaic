from django_mosaic.models import Author


def author(request):
    return {"author": Author.objects.prefetch_related("rel_me_links").first()}
