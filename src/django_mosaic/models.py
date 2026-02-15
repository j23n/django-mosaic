import bleach
import markdown
import secrets
from PIL import Image
from io import BytesIO
import os
import logging

import django.utils.timezone
from django.utils.text import slugify
from django.urls import reverse
from django.db import models
from django.utils.html import format_html
from django.contrib.auth.models import User
from django.core.files.base import ContentFile

logger = logging.getLogger("django_mosaic")


def generate_secret_id():
    return secrets.token_hex(32)


class Namespace(models.Model):
    name = models.SlugField(max_length=256, unique=True, blank=False, null=False)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<Namespace {self.name}>"


class Author(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    h_card = models.JSONField(default=dict, blank=True)
    display_name = models.CharField(max_length=256, blank=True, default="")
    url = models.URLField(max_length=512, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    photo_url = models.URLField(max_length=512, blank=True, default="")
    note = models.CharField(max_length=1024, blank=True, default="")

    def __str__(self):
        return self.user.username

    def __repr__(self):
        return f"<Author {self.user.username}>"


class RelMeLink(models.Model):
    author = models.ForeignKey(
        Author, on_delete=models.CASCADE, related_name="rel_me_links"
    )
    url = models.URLField(max_length=512)
    label = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.label or self.url


class ContentImage(models.Model):
    image = models.ImageField(upload_to="content/images/")
    thumb = models.ImageField(
        upload_to="content/images/", blank=True, null=True, editable=False
    )
    caption = models.CharField(max_length=2048, null=False, blank=True, default="")
    alt = models.CharField(max_length=2048, null=False, blank=True, default="")
    is_featured = models.BooleanField(default=False)
    post = models.ForeignKey("Post", on_delete=models.CASCADE)

    def __str__(self):
        return "Image"

    def __repr__(self):
        return f"<Image {self.image} [{self.alt[:50]}]>"

    def save(self, *args, **kwargs):
        # Only generate thumbnail on creation
        if not self.pk and self.image:
            try:
                # Generate random filename
                ext = self.image.name.split(".")[-1]
                random_name = secrets.token_hex(32)
                new_filename = f"{random_name}.{ext}"

                # Rename the image file
                self.image.name = new_filename

                # Generate thumbnail
                img = Image.open(self.image.file)
                img.thumbnail((600, 600), Image.Resampling.LANCZOS)

                thumb_io = BytesIO()
                img.save(thumb_io, format="PNG", quality=85)
                thumb_io.seek(0)

                # Save thumbnail with _thumb suffix
                thumb_filename = f"{random_name}_thumb.{ext}"
                self.thumb.save(
                    thumb_filename, ContentFile(thumb_io.read()), save=False
                )
            except (OSError, ValueError) as e:
                logger.warning(f"Failed to create thumbnail: {e}")

        super().save(*args, **kwargs)

    def markdown(self):
        if self.thumb:
            thumb = self.thumb
        else:
            thumb = self.image

        if self.caption:
            return format_html(
                "<figure><a href='{}'><img src='{}' alt='{}'></a>"
                "<figcaption>{}</figcaption></figure>",
                self.image.url,
                thumb.url,
                self.alt,
                self.caption,
            )
        return format_html(
            "<a href='{}'><img src='{}' alt='{}'></a>",
            self.image.url,
            thumb.url,
            self.alt,
        )


class Post(models.Model):
    author = models.ForeignKey(Author, on_delete=models.PROTECT)
    title = models.CharField(max_length=512, blank=False, null=False)
    content = models.TextField()
    draft_content = models.TextField(blank=True, null=True, default=None)
    slug = models.SlugField(max_length=256, blank=True, null=False, unique=True)
    summary = models.CharField(max_length=1024, null=False, blank=True)

    namespace = models.ForeignKey(
        "Namespace", on_delete=models.PROTECT, blank=False, null=False
    )
    is_published = models.BooleanField(default=False, blank=False, null=False)

    tags = models.ManyToManyField("Tag", blank=True)

    created_at = models.DateTimeField(auto_now_add=True, blank=False, null=False)
    published_at = models.DateTimeField(blank=True, null=True)
    changed_at = models.DateTimeField(auto_now=True, blank=False, null=False)

    secret_id = models.CharField(
        max_length=128,
        blank=False,
        null=False,
        unique=True,
        default=generate_secret_id,
    )

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        extra = set()

        # no longer update the slug once it's been published
        if not self.is_published and not self.slug:
            base = slugify(self.title)[:246]
            slug = base
            n = 2
            while Post.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
            extra.add("slug")
        if not self.summary:
            self.summary = bleach.clean(
                markdown.markdown(self.content), strip=True, tags={}
            )[:200]
            extra.add("summary")
        if self.is_published and not self.published_at:
            self.published_at = django.utils.timezone.now()
            extra.add("published_at")

        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | extra

        return super().save(*args, **kwargs)

    @property
    def has_draft(self):
        return self.draft_content is not None

    @property
    def featured_image(self):
        return self.contentimage_set.filter(is_featured=True).first() or self.contentimage_set.first()

    def get_absolute_url(self):
        if self.is_published:
            return reverse(
                "post-detail",
                args=[self.namespace.name, self.published_at.year, self.slug],
            )
        else:
            return reverse("draft-detail", args=[self.secret_id])

    def __str__(self):
        return f"{self.title}"

    def __repr__(self):
        date = self.published_at or self.created_at
        return f"<Post {self.title} - {date.year} [{self.namespace.name}]>"

    class Meta:
        ordering = ["-published_at"]


class Tag(models.Model):
    name = models.CharField(max_length=256, blank=False, null=False)
    slug = models.SlugField(max_length=256, blank=True, null=False)
    namespace = models.ForeignKey(
        "Namespace", on_delete=models.PROTECT, null=False, blank=False
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:256]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("tag-detail", args=[self.namespace.name, self.slug])

    def __str__(self):
        return f"{self.name} ({self.namespace.name})"

    def __repr__(self):
        return f"<Tag {self.name} [{self.namespace.name}]>"

    class Meta:
        unique_together = ("name", "namespace")
