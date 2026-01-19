from django.http import HttpResponseNotFound
from django.shortcuts import render, get_object_or_404
from blog.models import Post, Tag


def _public_posts():
    return Post.objects.filter(is_public=True)


def _private_posts():
    return Post.objects.filter(is_public=False)


def home(request):
    posts = _public_posts()[:10]
    tags = Tag.objects.filter(post__in=_public_posts())

    return render(request, "home.html", {"posts": posts, "tags": tags})


def post_list(request, visibility):
    if visibility == "public":
        posts = _public_posts()
    elif visibility == "private":
        posts = _private_posts()
    else:
        return HttpResponseNotFound()

    return render(request, "post-list.html", {"posts": posts})


def post_detail(request, visibility, year, post_slug):
    post = get_object_or_404(Post, slug=post_slug)

    return render(request, "post-detail.html", {"post": post})


def about(request):
    return render(request, "about.html")
