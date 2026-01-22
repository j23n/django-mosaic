from markdown import Markdown
from pathlib import Path
import yaml
import dateutil

from django.core.management.base import BaseCommand
from blog.models import Post


class Command(BaseCommand):
    help = "Imports markdown posts with a yaml header"

    def add_arguments(self, parser):
        parser.add_argument("path", type=Path)

    def handle(self, *args, **options):
        for file in options["path"]:
            with open(file, "r") as f:
                try:
                    file_content = f.read()
                    header, content = file_content.split("---")

                    header = yaml.load(header, Loader=yaml.Loader)
                    content = Markdown().convert(content)
                    post = Post(
                        title=header["title"],
                        published_at=dateutil.parser.parse(header["date"]),
                        slug=header["slug"],
                        summary=header["summary"],
                        content=content,
                    )
                    post.save()
                except Exception as e:
                    print(f"Could not import {file}: {e}")
