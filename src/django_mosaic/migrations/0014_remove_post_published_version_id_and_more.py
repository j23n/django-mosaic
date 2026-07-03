# Convert Post.published_version_id (IntegerField) into a proper ForeignKey
# to reversion.Version. The old int column and the FK share the exact column
# name (published_version_id), so this is a pure state change: the physical
# column already holds Version pks and is left in place, preserving every
# pinned-revision pointer across the upgrade. SET_NULL is enforced at the ORM
# level on Version deletion.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("django_mosaic", "0013_alter_contentimage_post"),
        ("reversion", "0002_add_index_on_version_for_content_type_and_db"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="post",
                    name="published_version_id",
                ),
                migrations.AddField(
                    model_name="post",
                    name="published_version",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="reversion.version",
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
