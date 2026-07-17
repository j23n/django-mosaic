"""Image upload endpoint shared by the martor editor and the admin dropzone.

Both callers POST multipart images and receive martor's JSON contract back:
``{"status": 200, "link": <url>, "name": <name>}`` on success, or
``{"status": 400, "error": <message>}`` on failure. Each upload creates a
ContentImage (processed/thumbnailed by ContentImage.save), attached to a Post
when one can be determined (explicit ``post_id`` field, or the admin change
page referer for martor's editor uploads).
"""

import logging
import re

from django.http import JsonResponse
from django.views.decorators.http import require_POST
from PIL import Image, UnidentifiedImageError

from django_mosaic.models import ContentImage, Post

logger = logging.getLogger("django_mosaic")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Matches the admin change page path so editor uploads attach to the post
# being edited, e.g. /admin/django_mosaic/post/42/change/
_CHANGE_PAGE_RE = re.compile(r"/post/(?P<pk>\d+)/change/")


def _resolve_post(request):
    post_id = request.POST.get("post_id")
    if not post_id:
        match = _CHANGE_PAGE_RE.search(request.META.get("HTTP_REFERER", ""))
        post_id = match.group("pk") if match else None
    # A non-numeric post_id would make the pk lookup raise ValueError -> 500;
    # treat anything that isn't a positive integer as "no post".
    if not post_id or not str(post_id).isdigit():
        return None
    return Post.objects.filter(pk=post_id).first()


@require_POST
def upload_image(request):
    upload = request.FILES.get("markdown-image-upload") or request.FILES.get("image")
    if upload is None:
        return JsonResponse({"status": 400, "error": "No image in request."})
    if upload.size > MAX_UPLOAD_BYTES:
        return JsonResponse(
            {"status": 400, "error": "Image exceeds the 20 MB upload limit."}
        )

    try:
        # verify() detects corrupt/non-image payloads before we persist
        # anything; the file is re-opened by ContentImage.save afterwards.
        Image.open(upload).verify()
        upload.seek(0)
    except (UnidentifiedImageError, OSError, ValueError):
        return JsonResponse({"status": 400, "error": "Not a valid image."})

    content_image = ContentImage(
        post=_resolve_post(request),
        image=upload,
        alt=request.POST.get("alt", ""),
    )
    try:
        content_image.save()
    except (OSError, ValueError) as e:
        logger.warning(f"Image upload failed: {e}")
        return JsonResponse({"status": 400, "error": "Could not process image."})

    return JsonResponse(
        {
            "status": 200,
            "link": content_image.image.url,
            "name": content_image.image.name.rsplit("/", 1)[-1],
        }
    )
