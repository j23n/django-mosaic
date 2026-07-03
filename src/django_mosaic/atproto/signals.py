"""Auto-publish hook: sync a Post to the PDS when it is saved as published.

Runs after the surrounding transaction commits and never lets a network
failure break the save — a PDS outage must not take the admin down. Failures
are logged; `manage.py atproto publish` re-syncs anything missed.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from django_mosaic.models import Post

from . import conf, publisher

logger = logging.getLogger("django_mosaic.atproto")


@receiver(post_save, sender=Post, dispatch_uid="mosaic_atproto_autopublish")
def sync_post_on_save(sender, instance, **kwargs):
    if not conf.enabled() or not conf.get_setting("AUTO_PUBLISH"):
        return

    def _sync():
        try:
            if publisher.syncable(instance):
                publisher.publish_post(instance)
            else:
                # Unpublished (or moved to a non-synced namespace): remove the
                # PDS record if one exists.
                publisher.unpublish_post(instance)
        except Exception as e:  # noqa: BLE001 - never break the admin save
            logger.error(f"ATProto sync failed for post {instance.pk}: {e}")

    transaction.on_commit(_sync)
