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
from django.contrib.auth.models import User
from django.core.files.base import ContentFile

logger = logging.getLogger("mosaic")


class Namespace(models.Model):
    name = models.SlugField(max_length=256, unique=True, blank=False, null=False)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<Namespace {self.name}>"


class Author(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    h_card = models.JSONField()

    def __str__(self):
        return self.user.username

    def __repr__(self):
        return f"<Author {self.user.username}>"


class ContentImage(models.Model):
    image = models.ImageField(upload_to="content/images/")
    thumb = models.ImageField(
        upload_to="content/images/", blank=True, null=True, editable=False
    )
    caption = models.CharField(max_length=2048, null=False, blank=True, default="")
    alt = models.CharField(max_length=2048, null=False, blank=True, default="")
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
                random_name = secrets.token_hex(16)
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
            except Exception as e:
                logger.warning(f"Failed to create thumbnail: {e}")

        super().save(*args, **kwargs)

    def markdown(self):
        if self.thumb:
            thumb = self.thumb
        else:
            thumb = self.image

        if self.caption:
            return f"<figure><a href='{self.image.url}'><img src='{thumb.url}' alt='{self.alt}'></a><figcaption>{self.caption}</figcaption></figure>"
        return (
            f"<a href='{self.image.url}'><img src='{thumb.url}' alt='{self.alt}'></a>"
        )


class Post(models.Model):
    author = models.ForeignKey(Author, on_delete=models.PROTECT)
    title = models.CharField(max_length=512, blank=False, null=False, unique=True)
    content = models.TextField()
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

    def save(self, *args, **kwargs):
        # no longer update the slug once it's been published
        if not self.is_published and not self.slug:
            self.slug = slugify(self.title)
        if not self.summary:
            self.summary = bleach.clean(
                markdown.markdown(self.content), strip=True, tags={}
            )[:200]
        if self.is_published and not self.published_at:
            self.published_at = django.utils.timezone.now()
        elif not self.is_published:
            self.published_at = None
        return super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse(
            "post-detail", args=[self.namespace.name, self.published_at.year, self.slug]
        )

    def __str__(self):
        return f"{self.title}"

    def __repr__(self):
        date = self.published_at or self.created_at
        return f"<Post {self.title} - {date.year} [{self.namespace.name}]>"

    class Meta:
        ordering = ["-published_at"]


class Tag(models.Model):
    name = models.CharField(max_length=256, blank=False, null=False)
    namespace = models.ForeignKey(
        "Namespace", on_delete=models.PROTECT, null=False, blank=False
    )

    def get_absolute_url(self):
        return reverse("tag-detail", args=[self.namespace.name, self.name])

    def __str__(self):
        return f"{self.name} ({self.namespace.name})"

    def __repr__(self):
        return f"<Tag {self.name} [{self.namespace.name}]>"

    class Meta:
        unique_together = ("name", "namespace")
