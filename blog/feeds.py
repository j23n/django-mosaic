from django.contrib.syndication.views import Feed
from django.urls import reverse
from django.conf import settings
from django.templatetags.static import static

from blog.models import Post


class PostFeed(Feed):
    title = settings.CONSTANTS["site"]["title"]
    link = "/"
    description = settings.CONSTANTS["site"]["description"]

    def items(self):
        return Post.objects.filter(is_public=True)

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.content

    # Firefox seems to require application/xml instead of application/rss+xml
    # to apply the stylesheet
    def __call__(self, request, *args, **kwargs):
        response = super().__call__(request, *args, **kwargs)
        response['Content-Type'] = 'application/xml; charset=utf-8'
        return response

    stylesheets = [static("feed/style.xslt")]
