import bleach
import markdown

import django.utils.timezone
from django.utils.text import slugify
from django.urls import reverse
from django.db import models
from django.contrib.auth.models import User


class Namespace(models.Model):
    name = models.SlugField(max_length=256, unique=True, blank=False, null=False)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<Namespace {self.name}"


class Author(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    h_card = models.JSONField()


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
            self.summary = bleach.clean(markdown.markdown(self.content), strip=True, tags={})[:200]
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
        return self.name

    def __repr__(self):
        return f"<Tag {self.name} [{self.namespace.name}]>"

    class Meta:
        unique_together = ("name", "namespace")
