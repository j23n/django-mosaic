from django.db import migrations

# The core views default to the "public" namespace and ship a token-gated
# "private" one, but nothing created those rows — so a freshly migrated project
# 404'd on "/" until an operator hand-made them in the admin. Seed them here so
# the documented first-run flow (migrate → createsuperuser → runserver) works.
DEFAULT_NAMESPACES = ("public", "private")


def create_default_namespaces(apps, schema_editor):
    Namespace = apps.get_model("django_mosaic", "Namespace")
    for name in DEFAULT_NAMESPACES:
        Namespace.objects.get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [
        ("django_mosaic", "0014_remove_post_published_version_id_and_more"),
    ]

    # Reverse is a no-op: the namespaces may hold posts by the time anyone
    # migrates back, so we never delete them automatically.
    operations = [
        migrations.RunPython(create_default_namespaces, migrations.RunPython.noop),
    ]
