from django.urls import path, include
from django_magic_authorization.urls import protected_path

from blog.views import post_list, post_detail, home, about, tag_detail
from blog.feeds import PostFeed

blog_patterns = [
    path("tag/<str:name>", tag_detail, name="tag-detail"),
    path("posts", post_list, name="post-list"),
    path("posts/<int:year>/<str:post_slug>", post_detail, name="post-detail"),
    path("feed", PostFeed()),
]

urlpatterns = [
    path("about", about, name="about"),
    path("", home, name="home"),
    path("<slug:namespace>/", include(blog_patterns)),
    protected_path("private/", include(blog_patterns), kwargs={"namespace": "private"}),
]
