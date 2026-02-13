import secrets

from django.db import migrations


HARDCODED_DEFAULT = (
    "5abf335a0aa2420cdecca6f0e53a85d4b3879b0cdb0cea2b43f864fafaed425a"
)


def regenerate_duplicates(apps, schema_editor):
    Post = apps.get_model("django_mosaic", "Post")
    for post in Post.objects.filter(secret_id=HARDCODED_DEFAULT):
        post.secret_id = secrets.token_hex(32)
        post.save(update_fields=["secret_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("django_mosaic", "0004_alter_post_secret_id_alter_post_title"),
    ]

    operations = [
        migrations.RunPython(regenerate_duplicates, migrations.RunPython.noop),
    ]
