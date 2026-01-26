from django.contrib import admin
from mosaic.models import Post, Tag


class PostAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
    list_display = ["title", "published_at", "namespace", "is_published", "created_at"]
    list_filter = ["is_published", "namespace", "tags", "published_at"]


class TagAdmin(admin.ModelAdmin):
    pass


admin.site.register(Post, PostAdmin)
admin.site.register(Tag, TagAdmin)
