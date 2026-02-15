from django.db import migrations, models
from django.utils.text import slugify


def populate_tag_slugs(apps, schema_editor):
    Tag = apps.get_model("django_mosaic", "Tag")
    for tag in Tag.objects.all():
        tag.slug = slugify(tag.name)[:256]
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
