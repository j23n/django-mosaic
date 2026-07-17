import logging
from pathlib import Path

import yaml
from dateutil import parser as date_parser
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from django_mosaic.models import Author, Namespace, Post, Tag

EXPECTED_KEYWORDS = ["title", "date", "draft"]

logger = logging.getLogger(__name__)


def _as_tag_list(value):
    """Normalize a YAML tag/category value into a list of strings.

    Accepts a comma-separated string ("a, b") or a YAML list (["a", "b"]).
    """
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, (list, tuple)):
        return [str(t).strip() for t in value if str(t).strip()]
    logger.warning(f"Could not process tags value {value!r}, unexpected type")
    return []


class Command(BaseCommand):
    help = "Imports markdown posts with a yaml header"

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)
        parser.add_argument("category", type=str)
        parser.add_argument(
            "--author",
            type=str,
            default=None,
            help="Username of the author to attribute imported posts to. "
            "Defaults to the only Author if exactly one exists.",
        )

    def _resolve_author(self, username):
        if username:
            try:
                return Author.objects.get(user__username=username)
            except Author.DoesNotExist as e:
                raise CommandError(f"No Author found for user '{username}'.") from e
        authors = list(Author.objects.all()[:2])
        if len(authors) == 1:
            return authors[0]
        if not authors:
            raise CommandError("No Author exists. Create one before importing posts.")
        raise CommandError(
            "Multiple Authors exist; specify one with --author <username>."
        )

    def handle(self, *args, **options):
        try:
            ns = Namespace.objects.get(name=options["category"])
        except Namespace.DoesNotExist as e:
            raise CommandError(
                f"Namespace '{options['category']}' does not exist."
            ) from e

        author = self._resolve_author(options["author"])

        for file in options["path"].glob("**/*.md"):
            logger.info(f"Importing {file}")
            try:
                with open(file) as f:
                    file_content = f.read()
                _, header_raw, content = file_content.split("---", maxsplit=2)
                header = yaml.safe_load(header_raw) or {}

                missing = [ek for ek in EXPECTED_KEYWORDS if ek not in header]
                if missing:
                    raise ValueError(
                        f"Missing expected metadata keys {missing}; "
                        f"found {list(header.keys())}"
                    )

                slug = header.get("slug", "")

                published_at = date_parser.parse(str(header["date"]))
                if timezone.is_naive(published_at):
                    published_at = timezone.make_aware(published_at)

                # Re-importing the same source should update the post, not
                # create a duplicate: key on (namespace, slug) when a slug is
                # given, else (namespace, title). Wrap each file so one bad row
                # (e.g. an IntegrityError) doesn't abort the whole batch.
                lookup = {"namespace": ns}
                if slug:
                    lookup["slug"] = slug
                else:
                    lookup["title"] = header["title"]
                fields = {
                    "author": author,
                    "title": header["title"],
                    "is_published": not header["draft"],
                    "published_at": published_at,
                    "summary": header.get("description", ""),
                    "content": content,
                }

                tag_names = _as_tag_list(header.get("tags"))
                tag_names += _as_tag_list(header.get("categories"))

                with transaction.atomic():
                    tags = [
                        Tag.objects.get_or_create(name=name, namespace=ns)[0]
                        for name in tag_names
                    ]
                    post, created = Post.objects.update_or_create(
                        defaults=fields, **lookup
                    )
                    post.tags.set(tags)
                verb = "Created" if created else "Updated"
                logger.info(f"{verb} post {post} with tags {tags}")
            except (ValueError, OSError, yaml.YAMLError, IntegrityError) as e:
                logger.error(f"Could not import {file}: {e}", exc_info=True)
