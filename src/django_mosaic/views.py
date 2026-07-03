from django.shortcuts import render, get_object_or_404
from reversion.models import Version
from django_mosaic.models import Namespace, Post, Tag, ContentImage


def _get_posts(namespace="public"):
    return (
        Post.objects.filter(namespace__name=namespace, is_published=True)
        .select_related("namespace", "author__user")
        .prefetch_related("tags")
    )


def home(request, namespace="public"):
    get_object_or_404(Namespace, name=namespace)
    posts = _get_posts(namespace)
    tags = Tag.objects.filter(post__in=posts).distinct().order_by("name")

    return render(
        request,
        [f"home-{namespace}.html", "home.html"],
        {"posts": posts, "tags": tags, "namespace": namespace},
    )


def post_list(request, namespace):
    posts = _get_posts(namespace)
    return render(request, "post-list.html", {"posts": posts, "namespace": namespace})


def post_detail(request, namespace, year, post_slug):
    post = get_object_or_404(
        Post.objects.select_related("namespace", "author__user"),
        slug=post_slug,
        namespace__name=namespace,
        is_published=True,
        published_at__year=year,
    )
    post.content = post.published_content

    next_post = (
        Post.objects.filter(
            namespace=post.namespace,
            is_published=True,
            published_at__gt=post.published_at,
        )
        .reverse()
        .first()
    )
    prev_post = Post.objects.filter(
        namespace=post.namespace,
        is_published=True,
        published_at__lt=post.published_at,
    ).first()

    return render(
        request,
        "post-detail.html",
        {
            "post": post,
            "next_post": next_post,
            "prev_post": prev_post,
            "namespace": namespace,
        },
    )


def draft_detail(request, namespace, secret_id):
    post = get_object_or_404(Post, secret_id=secret_id, namespace__name=namespace)

    versions = Version.objects.get_for_object(post)
    if versions.exists():
        post.content = versions.first().field_dict.get("content", post.content)

    response = render(
        request,
        "post-detail.html",
        {"post": post, "is_draft": True, "namespace": namespace},
    )
    response["Referrer-Policy"] = "no-referrer"
    return response


def tag_detail(request, namespace, slug):
    tag = get_object_or_404(Tag, slug=slug, namespace__name=namespace)

    posts = _get_posts(namespace).filter(tags=tag)
    images = ContentImage.objects.filter(post__in=posts).select_related("post")

    return render(
        request,
        [f"tags/{tag.slug}.html", "tag-detail.html"],
        {"posts": posts, "tag": tag, "images": images, "namespace": namespace},
    )


def robots_txt(request):
    return render(request, "robots.txt", content_type="text/plain")
