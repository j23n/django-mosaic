from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from django_magic_authorization.urls import protected_path

from django_mosaic.feeds import PostFeed
from django_mosaic.sitemaps import PostSitemap
from django_mosaic.views import (
    draft_detail,
    home,
    post_detail,
    post_list,
    robots_txt,
    tag_detail,
)

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
    # Martor editor endpoints (live preview). Must precede the namespace
    # catch-all or "martor" would be treated as a namespace.
    path("martor/", include("martor.urls")),
    path("<slug:namespace>/", include(mosaic_patterns)),
    protected_path(
        "private/", include(mosaic_patterns), kwargs={"namespace": "private"}
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
