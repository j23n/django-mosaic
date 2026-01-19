from django.urls import reverse
from django.db import models


class Post(models.Model):
    title = models.CharField(max_length=512, blank=False, null=False, unique=True)
    content = models.TextField()
    slug = models.SlugField(max_length=256, blank=False, null=False, unique=True)

    is_public = models.BooleanField(default=False)
    is_draft = models.BooleanField(default=True, blank=False, null=False)

    tags = models.ManyToManyField("Tag")

    created_at = models.DateTimeField(auto_now_add=True, blank=False, null=False)
    published_at = models.DateTimeField(blank=True, null=False)
    changed_at = models.DateTimeField(auto_now=True, blank=False, null=False)

    def visibility(self):
        if self.is_public:
            return "public"
        else:
            return "private"

    def get_absolute_url(self):
        return reverse(
            "post-detail", args=[self.visibility(), self.published_at.year, self.slug]
        )

    def __str__(self):
        return f"{self.title} - {self.created_at}"

    def __repr__(self):
        return f"<Post {self.title} [{self.created_at}] {self.is_public}>"

    class Meta:
        ordering = ["-published_at"]


class Tag(models.Model):
    name = models.CharField(max_length=256, blank=False, null=False, unique=True)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<Tag {self.name}>"
