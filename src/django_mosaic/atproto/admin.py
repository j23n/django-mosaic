from django.contrib import admin

from .models import DocumentRecord, PublicationRecord, WaitlistSignup


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
