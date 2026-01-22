from django.shortcuts import render, get_object_or_404
from django.conf import settings
from mosaic.models import Post, Tag


def home(request):
    posts = Post.objects.filter(namespace__name="public")
    tags = Tag.objects.filter(namespace__name="public")

    return render(
        request,
        "home.html",
        {"posts": posts, "tags": tags, "CONSTANTS": settings.CONSTANTS},
    )


def post_list(request, namespace):
    posts = Post.objects.filter(namespace__name=namespace)
    return render(
        request, "post-list.html", {"posts": posts, "CONSTANTS": settings.CONSTANTS}
    )


def post_detail(request, namespace, year, post_slug):
    post = get_object_or_404(Post, slug=post_slug)

    return render(
        request, "post-detail.html", {"post": post, "CONSTANTS": settings.CONSTANTS}
    )


def tag_detail(request, namespace, name):
    tag = get_object_or_404(Tag, name=name, namespace__name=namespace)

    posts = Post.objects.filter(tags__name=tag)

    return render(
        request,
        "tag-detail.html",
        {"posts": posts, "tag": tag, "CONSTANTS": settings.CONSTANTS},
    )


def about(request):
    return render(request, "about.html")
