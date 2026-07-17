from django.db import migrations, models
from django.utils.text import slugify


def populate_tag_slugs(apps, schema_editor):
    Tag = apps.get_model("django_mosaic", "Tag")
    # Slugs must end up unique per namespace (0012 adds that constraint), but
    # distinct names can slugify to the same string ("C++"/"C#" → "c") or to
    # empty. De-duplicate with numeric suffixes as we go, or 0012 fails.
    seen = set()
    for tag in Tag.objects.all().order_by("pk"):
        base = slugify(tag.name)[:256] or "tag"
        slug = base
        n = 2
        while (tag.namespace_id, slug) in seen:
            suffix = f"-{n}"
            slug = f"{base[: 256 - len(suffix)]}{suffix}"
            n += 1
        seen.add((tag.namespace_id, slug))
        tag.slug = slug
        tag.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("django_mosaic", "0008_post_draft_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="tag",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=256),
            preserve_default=False,
        ),
        migrations.RunPython(populate_tag_slugs, migrations.RunPython.noop),
    ]
