"""ATProto bridge operations.

Usage:
    python manage.py atproto publish            # sync all syncable posts
    python manage.py atproto publish --post 42  # sync one post
    python manage.py atproto unpublish --post 42
    python manage.py atproto status
"""

from django.core.management.base import BaseCommand, CommandError

from django_mosaic.models import Post
from django_mosaic.atproto import conf, publisher
from django_mosaic.atproto.client import Session
from django_mosaic.atproto.models import DocumentRecord, PublicationRecord


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

    def handle(self, *args, **options):
        command = options["command"]
        if command == "status":
            return self._status()

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
