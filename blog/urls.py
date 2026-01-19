from django.urls import path
from django_magic_authorization.urls import protected_path

from blog.views import post_list, post_detail, home, about
from blog.feeds import PostFeed

def protect_private(kwargs):
    return kwargs["visibility"] == "private"


urlpatterns = [
    path("about", about, name="about"),
    path("feed", PostFeed()),
    path("", home, name="home"),
    protected_path(
        "<str:visibility>", post_list, protect_fn=protect_private, name="post-list"
    ),
    path(
        "<str:visibility>/<int:year>/<str:post_slug>", post_detail, name="post-detail"
    ),
]
