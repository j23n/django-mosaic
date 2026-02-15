from django.urls import path, include
from django.contrib.sitemaps.views import sitemap
from django_magic_authorization.urls import protected_path
from django.conf import settings
from django.conf.urls.static import static

from django_mosaic.views import (
    post_list,
    post_detail,
    draft_detail,
    home,
    tag_detail,
    robots_txt,
)
from django_mosaic.feeds import PostFeed
from django_mosaic.sitemaps import PostSitemap

mosaic_patterns = [
    path("", home, name="ns-home"),
    path("tag/<slug:slug>", tag_detail, name="tag-detail"),
    path("posts", post_list, name="post-list"),
    path("posts/<int:year>/<str:post_slug>", post_detail, name="post-detail"),
    path("feed", PostFeed(), name="feed"),
    path("posts/drafts/<str:secret_id>", draft_detail, name="draft-detail"),
]

urlpatterns = [
    path("", home, name="home"),
    path("sitemap.xml", sitemap, {"sitemaps": {"posts": PostSitemap}}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots-txt"),
    path("<slug:namespace>/", include(mosaic_patterns)),
    protected_path(
        "private/", include(mosaic_patterns), kwargs={"namespace": "private"}
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
