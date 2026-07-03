from django.contrib.syndication.views import Feed
from django.conf import settings

from markdownify.templatetags.markdownify import markdownify

from django_mosaic.models import Post, Namespace


class PostFeed(Feed):
    link = "/"

    def title(self):
        return settings.CONSTANTS["site"]["title"]

    def description(self):
        return settings.CONSTANTS["site"]["description"]

    def get_object(self, request, namespace):
        return Namespace.objects.get(name=namespace)

    def items(self, obj):
        return Post.objects.filter(namespace=obj, is_published=True).select_related(
            "namespace"
        )[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return markdownify(item.published_content)

    def item_pubdate(self, item):
        return item.published_at
