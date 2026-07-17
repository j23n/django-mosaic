from django.conf import settings as django_settings

from django_mosaic.models import Author

# Shipped default so a consumer that installs django_mosaic without defining
# settings.CONSTANTS (e.g. into an existing project, skipping the scaffold)
# still renders pages and feeds instead of raising AttributeError/KeyError.
DEFAULT_SITE_CONSTANTS = {"title": "", "description": ""}


def site_constants():
    """settings.CONSTANTS with the ``site`` title/description defaulted."""
    constants = getattr(django_settings, "CONSTANTS", None) or {}
    site = {**DEFAULT_SITE_CONSTANTS, **(constants.get("site") or {})}
    return {**constants, "site": site}


def author(request):
    return {
        "author": Author.objects.prefetch_related("rel_me_links").first(),
        "CONSTANTS": site_constants(),
    }
