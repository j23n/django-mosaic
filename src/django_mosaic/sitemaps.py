from django.contrib.sitemaps import Sitemap

from django_mosaic.models import Post


def _protected_namespaces():
    """Namespace names served behind a ``protected_path``.

    Derived from django_magic_authorization's registry (the same source of
    truth the gate enforces on), so a second gated namespace — e.g.
    ``protected_path("family/", ...)`` — is kept out of the sitemap
    automatically instead of only the hardcoded "private". Falls back to
    {"private"} if the registry can't be read, so the default gated namespace
    is never leaked.
    """
    try:
        from django_magic_authorization.middleware import MagicAuthorizationRouter

        paths = MagicAuthorizationRouter().get_protected_paths()
    except Exception:  # noqa: BLE001 - registry unavailable: fail safe
        return {"private"}
    return {p.strip("/").split("/", 1)[0] for p in paths if p.strip("/")}


class PostSitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        # Every published post except those in a gated (token-protected)
        # namespace.
        return (
            Post.objects.filter(is_published=True)
            .exclude(namespace__name__in=_protected_namespaces())
            .select_related("namespace")
        )

    def lastmod(self, obj):
        return obj.changed_at
