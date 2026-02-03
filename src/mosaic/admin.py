from django.contrib import admin
from django.db import models
from django import forms
from mosaic.models import Post, Tag


class PostAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
    list_display = ["title", "published_at", "namespace", "is_published", "created_at"]
    list_filter = ["is_published", "namespace", "tags", "published_at"]

    formfield_overrides = {
        models.TextField: {'widget': forms.Textarea(attrs={'rows':'20', 'style':'max-height: none; width: 100%'})},
    }


class TagAdmin(admin.ModelAdmin):
    pass


admin.site.register(Post, PostAdmin)
admin.site.register(Tag, TagAdmin)
