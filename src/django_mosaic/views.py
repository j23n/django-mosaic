from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from reversion.models import Version

from django_mosaic.models import ContentImage, Namespace, Post, Tag


def _resolve_namespace(namespace):
    """Fetch a namespace by its exact, case-sensitive name (404 otherwise).

    The token gate that protects the "private" prefix
    (django_magic_authorization middleware) matches ``request.path``
    case-sensitively, but the database lookup ``namespace__name=…`` is
    case-*insensitive* under some collations (e.g. MySQL's default). Without an
    exact-case check, ``/PRIVATE/…`` would resolve the ``private`` namespace
    while slipping past the gate — serving gated content unauthenticated. Every
    view resolves the namespace through here first so a non-canonical casing
    404s regardless of database collation.
    """
    ns = get_object_or_404(Namespace, name=namespace)
    if ns.name != namespace:
        raise Http404("No namespace matches the given query.")
    return ns


def _get_posts(namespace="public"):
    return (
        Post.objects.filter(namespace__name=namespace, is_published=True)
        .select_related("namespace", "author__user")
        .prefetch_related("tags")
    )


def _paginate(request, queryset):
    """Paginate a queryset. `posts` stays iterable (a Page), and `page_obj`
    drives the pagination controls. Page size is MOSAIC_PAGE_SIZE (default 10).
    """
    per_page = getattr(settings, "MOSAIC_PAGE_SIZE", 10)
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


def home(request, namespace="public"):
    _resolve_namespace(namespace)
    all_posts = _get_posts(namespace)
    # Tags reflect the whole namespace, not just the current page.
    tags = Tag.objects.filter(post__in=all_posts).distinct().order_by("name")
    page = _paginate(request, all_posts)

    return render(
        request,
        [f"home-{namespace}.html", "home.html"],
        {"posts": page, "page_obj": page, "tags": tags, "namespace": namespace},
    )


def post_list(request, namespace):
    _resolve_namespace(namespace)
    page = _paginate(request, _get_posts(namespace))
    return render(
        request,
        "post-list.html",
        {"posts": page, "page_obj": page, "namespace": namespace},
    )


def post_detail(request, namespace, year, post_slug):
    _resolve_namespace(namespace)
    post = get_object_or_404(
        Post.objects.select_related("namespace", "author__user"),
        slug=post_slug,
        namespace__name=namespace,
        is_published=True,
        published_at__year=year,
    )
    post.content = post.published_content

    # Order by (published_at, id) so posts sharing a published_at still have a
    # stable neighbour instead of skipping each other, and pull the namespace
    # for the template's get_absolute_url without an extra query each.
    siblings = Post.objects.filter(
        namespace=post.namespace, is_published=True
    ).select_related("namespace")
    next_post = (
        siblings.filter(
            Q(published_at__gt=post.published_at)
            | Q(published_at=post.published_at, id__gt=post.id)
        )
        .order_by("published_at", "id")
        .first()
    )
    prev_post = (
        siblings.filter(
            Q(published_at__lt=post.published_at)
            | Q(published_at=post.published_at, id__lt=post.id)
        )
        .order_by("-published_at", "-id")
        .first()
    )

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
    _resolve_namespace(namespace)
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
    _resolve_namespace(namespace)
    tag = get_object_or_404(Tag, slug=slug, namespace__name=namespace)

    tagged = _get_posts(namespace).filter(tags=tag)
    images = ContentImage.objects.filter(post__in=tagged).select_related("post")
    page = _paginate(request, tagged)

    return render(
        request,
        [f"tags/{tag.slug}.html", "tag-detail.html"],
        {
            "posts": page,
            "page_obj": page,
            "tag": tag,
            "images": images,
            "namespace": namespace,
        },
    )


def robots_txt(request):
    return render(request, "robots.txt", content_type="text/plain")
