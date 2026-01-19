from django.contrib import admin
from blog.models import Post, Tag


class PostAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at"]
    list_display = ["created_at", "changed_at", "is_draft", "title", "slug"]


class TagAdmin(admin.ModelAdmin):
    pass


admin.site.register(Post, PostAdmin)
admin.site.register(Tag, TagAdmin)
