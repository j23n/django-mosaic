from django.db import models

from django_mosaic.models import Post


class PublicationRecord(models.Model):
    """Tracks the site.standard.publication record for this site (singleton)."""

    uri = models.CharField(max_length=512)
    cid = models.CharField(max_length=256)
    rkey = models.CharField(max_length=64)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.uri


class DocumentRecord(models.Model):
    """Tracks the site.standard.document (and companion post) for a Post."""

    post = models.OneToOneField(
        Post, on_delete=models.CASCADE, related_name="atproto_document"
    )
    uri = models.CharField(max_length=512)
    cid = models.CharField(max_length=256)
    rkey = models.CharField(max_length=64)
    bsky_post_uri = models.CharField(max_length=512, blank=True, default="")
    bsky_post_cid = models.CharField(max_length=256, blank=True, default="")
    published_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.uri
