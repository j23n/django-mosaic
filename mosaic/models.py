from django.urls import reverse
from django.db import models


class Namespace(models.Model):
    name = models.SlugField(max_length=256, unique=True, blank=False, null=False)


class Post(models.Model):
    title = models.CharField(max_length=512, blank=False, null=False, unique=True)
    content = models.TextField()
    slug = models.SlugField(max_length=256, blank=False, null=False, unique=True)
    summary = models.CharField(max_length=1024, null=False, blank=True)

    namespace = models.ForeignKey(
        "Namespace", on_delete=models.PROTECT, blank=False, null=False
    )
    is_draft = models.BooleanField(default=True, blank=False, null=False)

    tags = models.ManyToManyField("Tag")

    created_at = models.DateTimeField(auto_now_add=True, blank=False, null=False)
    published_at = models.DateTimeField(blank=True, null=False)
    changed_at = models.DateTimeField(auto_now=True, blank=False, null=False)

    def save(self, *args, **kwargs):
        if not self.summary:
            self.summary = self.content[:100]
        return super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse(
            "post-detail", args=[self.namespace.name, self.published_at.year, self.slug]
        )

    def __str__(self):
        return f"{self.title} - {self.created_at}"

    def __repr__(self):
        return f"<Post {self.title} - {self.created_at} [{self.namespace.name}]>"

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
