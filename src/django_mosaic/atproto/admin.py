from django.contrib import admin

from .models import DocumentRecord, OAuthSession, PublicationRecord, WaitlistSignup


@admin.register(WaitlistSignup)
class WaitlistSignupAdmin(admin.ModelAdmin):
    list_display = ["contact", "created_at"]
    readonly_fields = ["contact", "created_at"]


@admin.register(DocumentRecord)
class DocumentRecordAdmin(admin.ModelAdmin):
    list_display = ["post", "uri", "updated_at"]
    readonly_fields = ["post", "uri", "cid", "rkey", "bsky_post_uri", "bsky_post_cid"]


@admin.register(PublicationRecord)
class PublicationRecordAdmin(admin.ModelAdmin):
    list_display = ["uri", "updated_at"]


@admin.register(OAuthSession)
class OAuthSessionAdmin(admin.ModelAdmin):
    """Token material is deliberately not exposed — the admin shows who is
    connected and lets you revoke by deleting the row."""

    list_display = ["handle", "did", "scope", "access_token_expires_at", "updated_at"]
    fields = ["handle", "did", "pds_url", "auth_server", "scope", "created_at"]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False
