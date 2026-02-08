from django.contrib import admin
from django.db import models
from django import forms
from django.utils.html import format_html
from django_mosaic.models import Post, Tag, ContentImage


class ContentImageInlineAdmin(admin.TabularInline):
    model = ContentImage
    readonly_fields = ["thumb", "thumbnail_preview", "copy_markdown_button"]
    fields = ["image", "thumbnail_preview", "caption", "alt", "copy_markdown_button"]

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
                markdown_text.replace('"', "&quot;"),
            )
        return ""

    copy_markdown_button.short_description = "Markdown"


class PostAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
    list_display = ["title", "is_published", "published_at", "namespace", "get_tags", "changed_at"]
    list_filter = ["is_published", "namespace", "tags", "published_at"]

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


class ContentImageAdmin(admin.ModelAdmin):
    readonly_fields = ["image", "thumb"]
    list_display = ["alt", "caption", "post", "post__created_at"]


class TagAdmin(admin.ModelAdmin):
    pass


admin.site.register(Post, PostAdmin)
admin.site.register(Tag, TagAdmin)
admin.site.register(ContentImage, ContentImageAdmin)
