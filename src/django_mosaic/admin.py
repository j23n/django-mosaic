from django.contrib import admin, messages
from django.db import models
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django_mosaic.models import Post, Tag, ContentImage, Author, RelMeLink


class ContentImageInlineAdmin(admin.TabularInline):
    model = ContentImage
    readonly_fields = ["thumb", "thumbnail_preview", "copy_markdown_button"]
    fields = ["image", "thumbnail_preview", "caption", "alt", "is_featured", "copy_markdown_button"]

    def thumbnail_preview(self, obj):
        if obj.thumb:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 200px;" />',
                obj.thumb.url,
            )
        return "No thumbnail"

    thumbnail_preview.short_description = "Preview"

    def copy_markdown_button(self, obj):
        if obj.pk:
            markdown_text = obj.markdown()
            return format_html(
                '<button type="button" class="button" '
                'onclick="navigator.clipboard.writeText(this.dataset.markdown).then(() => '
                "{{ this.textContent = 'Copied!'; setTimeout(() => this.textContent = 'Copy Markdown', 1500) }})\""
                'data-markdown="{}">'
                "Copy Markdown"
                "</button>",
                markdown_text,
            )
        return ""

    copy_markdown_button.short_description = "Markdown"


class PostAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at", "draft_preview_link"]
    list_display = [
        "title",
        "is_published",
        "has_draft_indicator",
        "published_at",
        "namespace",
        "get_tags",
        "changed_at",
    ]
    list_filter = ["is_published", "namespace", "tags", "published_at"]
    actions = ["publish_draft"]

    formfield_overrides = {
        models.TextField: {
            "widget": forms.Textarea(
                attrs={"rows": "20", "style": "max-height: none; width: 100%"}
            )
        },
    }

    inlines = [ContentImageInlineAdmin]

    def get_tags(self, obj):
        return ", ".join([t.name for t in obj.tags.all()])

    def has_draft_indicator(self, obj):
        return "Draft pending" if obj.has_draft else ""

    has_draft_indicator.short_description = "Draft"

    def draft_preview_link(self, obj):
        if not obj.pk:
            return ""
        if obj.has_draft:
            url = reverse("draft-detail", args=[obj.namespace.name, obj.secret_id])
            return format_html('<a href="{}" target="_blank">Preview draft</a>', url)
        return "No draft pending"

    draft_preview_link.short_description = "Draft preview"

    @admin.action(description="Publish draft content")
    def publish_draft(self, request, queryset):
        published = 0
        skipped = 0
        for post in queryset:
            if post.has_draft:
                post.content = post.draft_content
                post.draft_content = None
                post.save(update_fields=["content", "draft_content"])
                published += 1
            else:
                skipped += 1
        if published:
            self.message_user(
                request,
                f"Published draft content for {published} post(s).",
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} post(s) with no draft content.",
                messages.WARNING,
            )


class ContentImageAdmin(admin.ModelAdmin):
    readonly_fields = ["image", "thumb"]
    list_display = ["alt", "caption", "post", "post__created_at"]


class TagAdmin(admin.ModelAdmin):
    pass


class RelMeLinkInline(admin.TabularInline):
    model = RelMeLink
    extra = 1


class AuthorAdmin(admin.ModelAdmin):
    fieldsets = [
        (
            "Public h-card",
            {
                "description": (
                    "These fields are rendered as your public h-card on the homepage."
                ),
                "fields": [
                    "user",
                    "display_name",
                    "url",
                    "email",
                    "photo_url",
                    "note",
                ],
            },
        ),
        (
            "Advanced",
            {
                "classes": ["collapse"],
                "fields": ["h_card"],
            },
        ),
    ]
    inlines = [RelMeLinkInline]


admin.site.register(Post, PostAdmin)
admin.site.register(Tag, TagAdmin)
admin.site.register(ContentImage, ContentImageAdmin)
admin.site.register(Author, AuthorAdmin)
admin.site.register(RelMeLink)
