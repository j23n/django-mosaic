from django.contrib.syndication.views import Feed
from markdownify.templatetags.markdownify import markdownify

from django_mosaic.context_processors import site_constants
from django_mosaic.models import Namespace, Post


class PostFeed(Feed):
    link = "/"

    def title(self):
        return site_constants()["site"]["title"]

    def description(self):
        return site_constants()["site"]["description"]

    def get_object(self, request, namespace):
        # Exact, case-sensitive match: a case-insensitive collation would
        # otherwise let /PRIVATE/feed resolve the gated `private` namespace and
        # leak its posts past the case-sensitive token gate (see
        # views._resolve_namespace).
        obj = Namespace.objects.get(name=namespace)
        if obj.name != namespace:
            raise Namespace.DoesNotExist
        return obj

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
