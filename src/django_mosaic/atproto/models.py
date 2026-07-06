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


class WaitlistSignup(models.Model):
    """A signup from the preview-service landing page (email or handle)."""

    contact = models.CharField(max_length=320)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["contact"], name="unique_waitlist_contact")
        ]

    def __str__(self):
        return self.contact


class OAuthSession(models.Model):
    """DPoP-bound ATProto OAuth tokens for one account (visitor or owner).

    The tokens and the per-session DPoP private key are secrets: anyone who
    can read this table can act as the account within the granted scope, so
    treat the database accordingly. One row per DID — signing in again
    replaces the previous grant.
    """

    did = models.CharField(max_length=256, unique=True)
    handle = models.CharField(max_length=256)
    pds_url = models.CharField(max_length=512)
    auth_server = models.CharField(max_length=512)
    token_endpoint = models.CharField(max_length=512)
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True, default="")
    scope = models.CharField(max_length=256, blank=True, default="")
    dpop_jwk = models.JSONField()
    dpop_pds_nonce = models.CharField(max_length=512, blank=True, default="")
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.handle} ({self.did})"


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
