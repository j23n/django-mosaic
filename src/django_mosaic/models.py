import html
import logging
import secrets
from io import BytesIO

import django.utils.timezone
import markdown
import reversion
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.html import format_html, strip_tags
from django.utils.text import slugify
from PIL import Image, ImageOps
from reversion.models import Version

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
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
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
    # Nullable: editor uploads on the "add post" form happen before the post
    # exists; they attach later (or stay unattached as library images).
    post = models.ForeignKey("Post", on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return "Image"

    def __repr__(self):
        return f"<Image {self.image} [{self.alt[:50]}]>"

    def _image_changed(self):
        """True if the image field differs from the persisted row."""
        if not self.pk:
            return True
        try:
            old = ContentImage.objects.get(pk=self.pk)
        except ContentImage.DoesNotExist:
            return True
        return old.image.name != self.image.name

    def save(self, *args, **kwargs):
        # Process the image on creation or whenever a new file is uploaded.
        if self.image and self._image_changed():
            try:
                random_name = secrets.token_hex(32)

                img = Image.open(self.image.file)
                # Fix orientation based on EXIF data
                img = ImageOps.exif_transpose(img)

                # JPEG cannot encode alpha/palette modes; normalize first.
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")

                # Resize main image to max 2048px on longest side (never upscales)
                img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

                # Save main image as JPEG
                main_io = BytesIO()
                img.save(main_io, format="JPEG", quality=90, optimize=True)
                main_io.seek(0)
                new_filename = f"{random_name}.jpg"
                self.image.save(new_filename, ContentFile(main_io.read()), save=False)

                # Generate thumbnail (never upscales)
                img.thumbnail((600, 600), Image.Resampling.LANCZOS)
                thumb_io = BytesIO()
                img.save(thumb_io, format="JPEG", quality=90, optimize=True)
                thumb_io.seek(0)
                thumb_filename = f"{random_name}_thumb.jpg"
                self.thumb.save(
                    thumb_filename, ContentFile(thumb_io.read()), save=False
                )
            except (OSError, ValueError, Image.DecompressionBombError) as e:
                logger.warning(f"Failed to process image: {e}")

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


@reversion.register(exclude=["published_version"])
class Post(models.Model):
    author = models.ForeignKey(Author, on_delete=models.PROTECT)
    title = models.CharField(max_length=512, blank=False, null=False)
    content = models.TextField()
    slug = models.SlugField(max_length=256, blank=True, null=False, unique=True)
    summary = models.CharField(max_length=1024, null=False, blank=True)
    # Pinned revision shown on the public site. FK (not a raw id) so a deleted
    # Version nulls the pointer instead of orphaning it; the raw-id accessor
    # `published_version_id` still works everywhere it did before.
    published_version = models.ForeignKey(
        "reversion.Version",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    namespace = models.ForeignKey(
        "Namespace", on_delete=models.PROTECT, blank=False, null=False
    )
    is_published = models.BooleanField(default=False, blank=False, null=False)

    tags = models.ManyToManyField("Tag", blank=True)

    created_at = models.DateTimeField(auto_now_add=True, blank=False, null=False)
    published_at = models.DateTimeField(blank=True, null=True, db_index=True)
    changed_at = models.DateTimeField(auto_now=True, blank=False, null=False)

    secret_id = models.CharField(
        max_length=128,
        blank=False,
        null=False,
        unique=True,
        default=generate_secret_id,
    )

    def clean(self):
        super().clean()
        # Keep the instance consistent with the published_post_has_published_at
        # constraint, which is validated during full_clean() (e.g. admin forms)
        # before save() would otherwise stamp this.
        if self.is_published and not self.published_at:
            self.published_at = django.utils.timezone.now()

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        extra = set()

        # Ensure every post has a slug. Once set (e.g. after first publish)
        # it is never regenerated, so permalinks stay stable.
        if not self.slug:
            base = slugify(self.title)[:246] or secrets.token_hex(4)
            slug = base
            n = 2
            while Post.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
            extra.add("slug")
        if not self.summary:
            # Render markdown, strip to plain text, and unescape entities so
            # the meta-description summary isn't double-escaped by the template.
            plain = html.unescape(strip_tags(markdown.markdown(self.content)))
            self.summary = plain[:200].strip()
            extra.add("summary")
        if self.is_published and not self.published_at:
            self.published_at = django.utils.timezone.now()
            extra.add("published_at")

        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | extra

        return super().save(*args, **kwargs)

    @cached_property
    def featured_image(self):
        images = list(self.contentimage_set.all())
        for image in images:
            if image.is_featured:
                return image
        return images[0] if images else None

    @property
    def published_content(self):
        if self.published_version_id is not None:
            try:
                # Scope to this post's own versions so a corrupted pointer
                # can't surface another object's content.
                version = Version.objects.get_for_object(self).get(
                    pk=self.published_version_id
                )
                return version.field_dict.get("content", self.content)
            except Version.DoesNotExist:
                pass
        return self.content

    def get_absolute_url(self):
        if self.is_published:
            return reverse(
                "post-detail",
                args=[self.namespace.name, self.published_at.year, self.slug],
            )
        else:
            return reverse("draft-detail", args=[self.namespace.name, self.secret_id])

    def __str__(self):
        return f"{self.title}"

    def __repr__(self):
        date = self.published_at or self.created_at
        return f"<Post {self.title} - {date.year} [{self.namespace.name}]>"

    class Meta:
        ordering = ["-published_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(is_published=False)
                | models.Q(published_at__isnull=False),
                name="published_post_has_published_at",
            )
        ]


class Tag(models.Model):
    name = models.CharField(max_length=256, blank=False, null=False)
    slug = models.SlugField(max_length=256, blank=True, null=False)
    namespace = models.ForeignKey(
        "Namespace", on_delete=models.PROTECT, null=False, blank=False
    )

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:250] or secrets.token_hex(4)
            slug = base
            n = 2
            while (
                Tag.objects.filter(slug=slug, namespace=self.namespace)
                .exclude(pk=self.pk)
                .exists()
            ):
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("tag-detail", args=[self.namespace.name, self.slug])

    def __str__(self):
        return f"{self.name} ({self.namespace.name})"

    def __repr__(self):
        return f"<Tag {self.name} [{self.namespace.name}]>"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name", "namespace"], name="unique_tag_name_per_namespace"
            ),
            models.UniqueConstraint(
                fields=["slug", "namespace"], name="unique_tag_slug_per_namespace"
            ),
        ]


@receiver(post_delete, sender=ContentImage)
def _delete_contentimage_files(sender, instance, **kwargs):
    """Remove the stored image/thumbnail files when a ContentImage row is
    deleted (directly or via Post cascade), so media doesn't accumulate."""
    for field in (instance.image, instance.thumb):
        if not field:
            continue
        try:
            field.delete(save=False)
        except Exception as e:  # noqa: BLE001 - deletion is best-effort
            logger.warning(f"Failed to delete image file {field.name}: {e}")
