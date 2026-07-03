"""Tests for the mosaic-admin project scaffolder."""

import compileall
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from django_mosaic import scaffold


class ScaffoldTest(SimpleTestCase):
    def test_init_writes_a_complete_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            scaffold.init(tmp)
            root = Path(tmp)
            for name in (
                "manage.py",
                "settings.py",
                "urls.py",
                "wsgi.py",
                "asgi.py",
                ".env",
                "templates/includes/header.html",
                "templates/includes/about.html",
            ):
                self.assertTrue((root / name).exists(), f"missing {name}")
            self.assertTrue((root / "static").is_dir())
            self.assertTrue((root / "media").is_dir())

            # manage.py must be executable and every generated .py must compile.
            self.assertTrue((root / "manage.py").stat().st_mode & 0o111)
            self.assertTrue(
                compileall.compile_dir(str(root), quiet=1, force=True),
                "generated project files must be valid Python",
            )

            settings_text = (root / "settings.py").read_text()
            self.assertIn("django_mosaic", settings_text)
            self.assertIn("MagicAuthorizationMiddleware", settings_text)

    def test_init_skips_existing_files_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "settings.py"
            marker.write_text("# my custom settings\n")
            scaffold.init(tmp)
            self.assertEqual(marker.read_text(), "# my custom settings\n")

            scaffold.init(tmp, force=True)
            self.assertIn("django_mosaic", marker.read_text())

    def test_cli_requires_subcommand(self):
        with self.assertRaises(SystemExit):
            scaffold.main([])
