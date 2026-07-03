from django.contrib.sitemaps import Sitemap

from django_mosaic.models import Post


class PostSitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        # Every published post except those in the gated "private" namespace.
        return (
            Post.objects.filter(is_published=True)
            .exclude(namespace__name="private")
            .select_related("namespace")
        )

    def lastmod(self, obj):
        return obj.changed_at
