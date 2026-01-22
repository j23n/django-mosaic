from django.contrib import admin
from mosaic.models import Post, Tag


class PostAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at", "slug", "published_at"]
    list_display = ["title", "created_at", "namespace", "is_draft", "published_at"]


class TagAdmin(admin.ModelAdmin):
    pass


admin.site.register(Post, PostAdmin)
admin.site.register(Tag, TagAdmin)
