from django.contrib.syndication.views import Feed
from django.conf import settings
from django.templatetags.static import static

from mosaic.models import Post, Namespace


class PostFeed(Feed):
    title = settings.CONSTANTS["site"]["title"]
    link = "/"
    description = settings.CONSTANTS["site"]["description"]

    def get_object(self, request, namespace):
        return Namespace.objects.get(name=namespace)

    def items(self, obj):
        return Post.objects.filter(namespace=obj)

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.content

    def item_pubdate(self, item):
        return item.published_at

    # Firefox seems to require application/xml instead of application/rss+xml
    # to apply the stylesheet
    def __call__(self, request, *args, **kwargs):
        response = super().__call__(request, *args, **kwargs)
        response["Content-Type"] = "application/xml; charset=utf-8"
        return response

    stylesheets = [static("feed/style.xslt")]
