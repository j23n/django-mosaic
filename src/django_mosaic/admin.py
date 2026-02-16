import reversion
from reversion.admin import VersionAdmin
from reversion.models import Version

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


class PostAdminForm(forms.ModelForm):
    published_version = forms.ChoiceField(
        required=False,
        label="Published revision",
        help_text="Select which revision is shown on the public site.",
    )

    class Meta:
        model = Post
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "--- latest (no pinned revision) ---")]
        if self.instance and self.instance.pk:
            versions = Version.objects.get_for_object(self.instance)
            for v in versions:
                snippet = v.field_dict.get("content", "")[:60]
                label = f"#{v.pk} — {v.revision.date_created:%Y-%m-%d %H:%M} — {snippet}"
                choices.append((str(v.pk), label))
        self.fields["published_version"].choices = choices
        if self.instance and self.instance.published_version_id is not None:
            self.fields["published_version"].initial = str(
                self.instance.published_version_id
            )


class PostAdmin(VersionAdmin):
    form = PostAdminForm
    readonly_fields = ["created_at", "draft_preview_link"]
    list_display = [
        "title",
        "is_published",
        "published_at",
        "namespace",
        "get_tags",
        "changed_at",
    ]
    list_filter = ["is_published", "namespace", "tags", "published_at"]
    actions = ["publish_revision"]

    formfield_overrides = {
        models.TextField: {
            "widget": forms.Textarea(
                attrs={"rows": "20", "style": "max-height: none; width: 100%"}
            )
        },
    }

    inlines = [ContentImageInlineAdmin]

    change_form_template = "admin/django_mosaic/post/change_form.html"

    def get_tags(self, obj):
        return ", ".join([t.name for t in obj.tags.all()])

    def draft_preview_link(self, obj):
        if not obj.pk:
            return ""
        url = reverse("draft-detail", args=[obj.namespace.name, obj.secret_id])
        return format_html('<a href="{}" target="_blank">Preview latest revision</a>', url)

    draft_preview_link.short_description = "Draft preview"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if "_publish" in request.POST:
            latest = Version.objects.get_for_object(obj).first()
            if latest:
                obj.published_version_id = latest.pk
                obj.save(update_fields=["published_version_id"])
        else:
            chosen = form.cleaned_data.get("published_version")
            obj.published_version_id = int(chosen) if chosen else None
            obj.save(update_fields=["published_version_id"])

    @admin.action(description="Publish latest revision")
    def publish_revision(self, request, queryset):
        published = 0
        skipped = 0
        for post in queryset:
            versions = Version.objects.get_for_object(post)
            if not versions.exists():
                skipped += 1
                continue
            latest = versions.first()
            post.published_version_id = latest.pk
            post.is_published = True
            post.save(update_fields=["published_version_id", "is_published"])
            published += 1
        if published:
            self.message_user(
                request,
                f"Published latest revision for {published} post(s).",
                messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} post(s) (no revisions found).",
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
