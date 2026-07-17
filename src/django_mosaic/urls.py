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

# The blog routes live under the "mosaic:" URL namespace (reverse("mosaic:home"),
# {% url 'mosaic:post-detail' %}), so mosaic's short, common names — home, feed,
# post-detail — never collide with the consumer project's own global names.
blog_patterns = [
    path("", home, name="home"),
    path("sitemap.xml", sitemap, {"sitemaps": {"posts": PostSitemap}}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots-txt"),
    path("<slug:namespace>/", include(mosaic_patterns)),
    protected_path(
        "private/", include(mosaic_patterns), kwargs={"namespace": "private"}
    ),
]

urlpatterns = [
    # Martor's editor endpoints are reversed by martor itself with bare names
    # (e.g. "martor_markdownfy"), so they must stay OUT of the mosaic namespace.
    # Kept before the blog include so "martor/" isn't captured as a namespace.
    path("martor/", include("martor.urls")),
    path("", include((blog_patterns, "mosaic"))),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
