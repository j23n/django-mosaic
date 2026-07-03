"""ATProto bridge operations.

Usage:
    python manage.py atproto publish            # sync all syncable posts
    python manage.py atproto publish --post 42  # sync one post
    python manage.py atproto unpublish --post 42
    python manage.py atproto status
    python manage.py atproto warm               # refresh cached reactions
    python manage.py atproto check --post 42    # probe live reaction APIs
"""

import json

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from django_mosaic.atproto import conf, publisher, reactions
from django_mosaic.atproto.client import Session
from django_mosaic.atproto.models import DocumentRecord, PublicationRecord
from django_mosaic.models import Post


class Command(BaseCommand):
    help = "Sync posts with the configured ATProto PDS"

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="command", required=True)

        publish = sub.add_parser("publish", help="Publish posts to the PDS")
        publish.add_argument("--post", type=int, help="Only this post id")

        unpublish = sub.add_parser(
            "unpublish", help="Delete a post's records from the PDS"
        )
        unpublish.add_argument("--post", type=int, required=True)
        unpublish.add_argument(
            "--delete-companion",
            action="store_true",
            help="Also delete the companion Bluesky post",
        )

        sub.add_parser("status", help="Show bridge status")

        warm = sub.add_parser(
            "warm", help="Refresh the cached reactions for synced posts"
        )
        warm.add_argument("--post", type=int, help="Only this post id")

        check = sub.add_parser(
            "check", help="Probe the live reaction APIs and print raw shapes"
        )
        check.add_argument(
            "--post", type=int, required=True, help="A synced post id to probe"
        )

    def handle(self, *args, **options):
        command = options["command"]
        if command == "status":
            return self._status()
        if command == "warm":
            return self._warm(options.get("post"))
        if command == "check":
            return self._check(options["post"])

        if not conf.enabled():
            raise CommandError(
                "MOSAIC_ATPROTO is not configured (HANDLE and APP_PASSWORD "
                "are required)."
            )

        if command == "publish":
            self._publish(options.get("post"))
        elif command == "unpublish":
            self._unpublish(options["post"], options["delete_companion"])

    def _publish(self, post_id):
        if post_id:
            posts = Post.objects.filter(pk=post_id)
            if not posts:
                raise CommandError(f"Post {post_id} does not exist.")
        else:
            posts = Post.objects.filter(
                is_published=True,
                namespace__name__in=conf.get_setting("NAMESPACES"),
            )

        session = Session.create()
        for post in posts:
            if not publisher.syncable(post):
                self.stdout.write(
                    f"  - skipping {post.pk} ({post.title}): not syncable"
                )
                continue
            record = publisher.publish_post(post, session=session)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {post.title} -> {record.uri}"))

    def _unpublish(self, post_id, delete_companion):
        post = Post.objects.filter(pk=post_id).first()
        if not post:
            raise CommandError(f"Post {post_id} does not exist.")
        publisher.unpublish_post(post, delete_companion=delete_companion)
        self.stdout.write(self.style.SUCCESS(f"  ✓ removed records for {post.title}"))

    def _status(self):
        self.stdout.write(f"Configured: {conf.enabled()}")
        self.stdout.write(f"Handle: {conf.get_setting('HANDLE') or '(unset)'}")
        publication = PublicationRecord.objects.first()
        self.stdout.write(f"Publication record: {publication or '(none)'}")
        self.stdout.write(f"Documents tracked: {DocumentRecord.objects.count()}")

    def _warm(self, post_id):
        """Force a live reaction fetch into the cache for synced posts, so the
        render path can run with REACTIONS_BLOCKING=False (cache-only)."""
        qs = DocumentRecord.objects.select_related("post")
        if post_id:
            qs = qs.filter(post_id=post_id)
        count = 0
        for document in qs:
            # Clear so blocking fetches refresh rather than read stale cache.
            cache.delete(f"mosaic_atproto:thread:{document.bsky_post_uri}")
            reactions.reactions_for(document.post, blocking=True)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"  ✓ warmed {count} post(s)"))

    def _check(self, post_id):
        """Fetch the live reaction sources for one post and print both the raw
        API shapes and mosaic's parsed result, so the parsers can be validated
        against the real services (which the sandbox could not reach)."""
        post = Post.objects.filter(pk=post_id).first()
        if not post:
            raise CommandError(f"Post {post_id} does not exist.")
        document = getattr(post, "atproto_document", None)
        if document is None:
            raise CommandError(f"Post {post_id} has no synced ATProto document.")

        from django_mosaic.atproto.client import xrpc_get

        self.stdout.write(self.style.WARNING("== getPostThread (raw) =="))
        try:
            raw = xrpc_get(
                reactions.APPVIEW_URL,
                "app.bsky.feed.getPostThread",
                {"uri": document.bsky_post_uri, "depth": 2},
            )
            self.stdout.write(json.dumps(raw, indent=2)[:2000])
            self.stdout.write(self.style.SUCCESS("-- parsed --"))
            self.stdout.write(str(reactions.fetch_thread(document.bsky_post_uri)))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"  getPostThread failed: {e}"))

        self.stdout.write(self.style.WARNING("\n== Constellation /links/all (raw) =="))
        try:
            import requests

            resp = requests.get(
                f"{reactions.CONSTELLATION_URL}/links/all",
                params={"target": document.uri},
                timeout=conf.get_setting("TIMEOUT"),
            )
            resp.raise_for_status()
            self.stdout.write(json.dumps(resp.json(), indent=2)[:2000])
            self.stdout.write(self.style.SUCCESS("-- parsed --"))
            self.stdout.write(str(reactions.fetch_crossapp_counts([document.uri])))
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"  Constellation failed: {e}"))
