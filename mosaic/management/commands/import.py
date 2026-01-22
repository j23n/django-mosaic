from pathlib import Path
import yaml
import dateutil
import logging

from django.core.management.base import BaseCommand
from django.utils.text import slugify
from mosaic.models import Post, Tag, Namespace

EXPECTED_KEYWORDS = ["title", "date", "draft"]

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Imports markdown posts with a yaml header"

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)
        parser.add_argument("category", type=str)

    def handle(self, *args, **options):
        ns = Namespace.objects.get(name=options["category"])

        for file in options["path"].glob("**/*.md"):
            logger.info(f"Importing {file}")
            with open(file, "r") as f:
                try:
                    file_content = f.read()
                    _, header, content = file_content.split("---", maxsplit=2)

                    header = yaml.load(header, Loader=yaml.BaseLoader)

                    if not all(ek in header.keys() for ek in EXPECTED_KEYWORDS):
                        raise ValueError(
                            f"Could not find all expected keywords in post metadata. Expected {EXPECTED_KEYWORDS}, found {header.keys()}"
                        )

                    slug = header.get("slug", slugify(header["title"]))

                    tags = []
                    header_tags = [t.strip() for t in header.get("tags", "").split(",") if t]
                    header_categories = [c.strip() for c in header.get("categories", "").split(",") if c]
                    header_tags.extend(header_categories)

                    if header_tags:
                        for t in header_tags:
                            if not isinstance(t, str):
                                logger.warning(
                                    f"Could not process tag {t}, not a string"
                                )
                                continue

                            t, _ = Tag.objects.get_or_create(name=t, namespace=ns)
                            tags.append(t)

                    post = Post(
                        title=header["title"],
                        is_draft=header["draft"],
                        published_at=dateutil.parser.parse(header["date"]),
                        slug=slug,
                        namespace=ns,
                        summary=header.get("description", ""),
                        content=content,
                    )
                    post.save()
                    for t in tags:
                        post.tags.add(t)
                    logger.info(f"Created post {post} with tags {tags}")
                except Exception as e:
                    logger.error(f"Could not import {file}: {e}", exc_info=True)
