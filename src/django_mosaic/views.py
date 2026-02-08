from django.shortcuts import render, get_object_or_404
from django.conf import settings
from django_mosaic.models import Post, Tag


def _get_posts(namespace="public"):
    return Post.objects.filter(namespace__name=namespace, is_published=True)


def home(request):
    posts = _get_posts()
    tags = Tag.objects.filter(post__in=posts).distinct()

    return render(
        request,
        "home.html",
        {"posts": posts, "tags": tags, "CONSTANTS": settings.CONSTANTS},
    )


def post_list(request, namespace):
    posts = _get_posts(namespace)
    return render(
        request, "post-list.html", {"posts": posts, "CONSTANTS": settings.CONSTANTS}
    )


def post_detail(request, namespace, year, post_slug):
    post = get_object_or_404(Post, slug=post_slug)

    next_post = Post.objects.filter(namespace=post.namespace, published_at__gt=post.published_at).reverse().first()
    prev_post = Post.objects.filter(namespace=post.namespace, published_at__lt=post.published_at).first()

    return render(
        request, "post-detail.html", {"post": post, "next_post": next_post, "prev_post": prev_post, "CONSTANTS": settings.CONSTANTS}
    )


def tag_detail(request, namespace, name):
    tag = get_object_or_404(Tag, name=name, namespace__name=namespace)

    posts = _get_posts(namespace).filter(tags__name=tag)

    return render(
        request,
        "tag-detail.html",
        {"posts": posts, "tag": tag, "CONSTANTS": settings.CONSTANTS},
    )


def about(request):
    return render(request, "about.html")
