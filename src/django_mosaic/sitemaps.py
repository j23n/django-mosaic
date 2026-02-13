from django.contrib.sitemaps import Sitemap

from django_mosaic.models import Post


class PostSitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        return Post.objects.filter(is_published=True, namespace__name="public")

    def lastmod(self, obj):
        return obj.changed_at
