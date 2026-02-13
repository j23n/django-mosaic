from django.conf import settings as django_settings

from django_mosaic.models import Author


def author(request):
    return {
        "author": Author.objects.prefetch_related("rel_me_links").first(),
        "CONSTANTS": django_settings.CONSTANTS,
    }
