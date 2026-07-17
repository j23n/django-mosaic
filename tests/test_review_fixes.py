"""Regression tests for issues found in the code review."""

import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from PIL import Image

from django_mosaic.models import Author, ContentImage, Namespace, Post, Tag


class SlugGenerationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("sluguser")
        cls.author = Author.objects.create(user=user)

    def test_published_post_without_slug_still_gets_slug(self):
        # H2: previously the slug guard skipped published posts, yielding an
        # empty slug and a NoReverseMatch / sitemap 500.
        post = Post.objects.create(
            author=self.author,
            title="Directly Published",
            namespace=self.ns,
            content="body",
            is_published=True,
        )
        self.assertTrue(post.slug)
        self.assertIn("directly-published", post.slug)
        # get_absolute_url must resolve without raising.
        self.assertTrue(post.get_absolute_url())

    def test_untitled_slugify_falls_back_to_token(self):
        post = Post.objects.create(
            author=self.author,
            title="日本語のみ",  # slugifies to empty
            namespace=self.ns,
            content="body",
            is_published=True,
        )
        self.assertTrue(post.slug)
        self.assertTrue(post.get_absolute_url())


class PublishedAtConstraintTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("cuser")
        cls.author = Author.objects.create(user=user)

    def test_bulk_update_to_published_without_date_is_rejected(self):
        # M8: queryset.update() bypasses save()/clean(); the DB constraint must
        # still refuse a published row with NULL published_at.
        post = Post.objects.create(
            author=self.author,
            title="Draft",
            slug="draft-x",
            namespace=self.ns,
            content="body",
            is_published=False,
        )
        self.assertIsNone(post.published_at)
        with self.assertRaises(IntegrityError):
            Post.objects.filter(pk=post.pk).update(is_published=True)


class TagSlugCollisionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]

    def test_distinct_names_same_slug_do_not_collide(self):
        # M7: "My Tag" and "my tag" both slugify to "my-tag".
        t1 = Tag.objects.create(name="My Tag", namespace=self.ns)
        t2 = Tag.objects.create(name="my tag", namespace=self.ns)
        self.assertNotEqual(t1.slug, t2.slug)
        # Tag detail lookup must not raise MultipleObjectsReturned.
        self.assertTrue(t1.get_absolute_url())
        self.assertTrue(t2.get_absolute_url())


class ContentImageProcessingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("imguser")
        cls.author = Author.objects.create(user=user)
        cls.post = Post.objects.create(
            author=cls.author,
            title="Img Post",
            slug="img-post",
            namespace=cls.ns,
            content="body",
        )

    def _upload(self, mode, color):
        buf = io.BytesIO()
        Image.new(mode, (120, 90), color=color).save(buf, format="PNG")
        buf.seek(0)
        return SimpleUploadedFile("x.png", buf.read(), content_type="image/png")

    def test_rgba_png_is_converted_and_thumbnailed(self):
        # M4: RGBA previously raised "cannot write mode RGBA as JPEG", leaving
        # no thumbnail.
        ci = ContentImage.objects.create(
            post=self.post,
            image=self._upload("RGBA", (10, 20, 30, 128)),
            alt="rgba",
        )
        self.assertTrue(ci.image.name.endswith(".jpg"))
        self.assertTrue(ci.thumb)
        self.assertTrue(ci.thumb.name.endswith("_thumb.jpg"))

    def test_unprocessable_upload_is_not_stored_as_active_content(self):
        # An undecodable payload with an executable name must never be persisted
        # under that name (it would be served as HTML/SVG from the media origin).
        bad = SimpleUploadedFile(
            "x.html", b"<script>alert(1)</script>", content_type="image/png"
        )
        ci = ContentImage.objects.create(post=self.post, image=bad, alt="x")
        self.assertTrue(ci.image.name.endswith(".jpg"))
        self.assertNotIn(".html", ci.image.name)


class UploadEndpointTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        cls.user = User.objects.create_superuser("up", "u@b.com", "pass")
        cls.author = Author.objects.create(user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def _png(self):
        buf = io.BytesIO()
        Image.new("RGB", (10, 10), color=(1, 2, 3)).save(buf, format="PNG")
        buf.seek(0)
        return SimpleUploadedFile("a.png", buf.read(), content_type="image/png")

    def test_non_numeric_post_id_does_not_500(self):
        # A non-numeric post_id used to raise ValueError from the pk lookup.
        resp = self.client.post(
            "/admin/django_mosaic/post/upload-image/",
            {"markdown-image-upload": self._png(), "post_id": "not-a-number"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], 200)


class ConstantsDefaultTest(TestCase):
    def test_empty_constants_gets_defaulted_site(self):
        from django.test import override_settings

        from django_mosaic.context_processors import site_constants

        # A consumer that never defined CONSTANTS["site"] must not crash feeds
        # or the context processor.
        with override_settings(CONSTANTS={}):
            data = site_constants()
        self.assertEqual(data["site"]["title"], "")
        self.assertEqual(data["site"]["description"], "")

    def test_provided_title_passes_through_with_defaulted_description(self):
        from django.test import override_settings

        from django_mosaic.context_processors import site_constants

        with override_settings(CONSTANTS={"site": {"title": "My Site"}}):
            data = site_constants()
        self.assertEqual(data["site"]["title"], "My Site")
        self.assertEqual(data["site"]["description"], "")


class ImportIdempotencyTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("importer")
        cls.author = Author.objects.create(user=user)

    def _write_post(self, tmp, slug="hello"):
        (tmp / "p.md").write_text(
            "---\n"
            "title: Hello\n"
            "date: 2026-01-02\n"
            "draft: false\n"
            f"slug: {slug}\n"
            "---\n"
            "Body text\n"
        )

    def test_reimport_updates_instead_of_duplicating(self):
        import tempfile
        from pathlib import Path

        from django.core.management import call_command

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            self._write_post(tmp)
            call_command("import", str(tmp), "public")
            call_command("import", str(tmp), "public")
        self.assertEqual(Post.objects.filter(namespace=self.ns).count(), 1)


class UrlNamespaceTest(TestCase):
    def test_blog_names_are_under_the_mosaic_namespace(self):
        from django.urls import NoReverseMatch, reverse

        # The blog routes are reachable as "mosaic:<name>"...
        self.assertEqual(reverse("mosaic:home"), "/")
        self.assertTrue(
            reverse("mosaic:post-detail", args=["public", 2026, "hi"]).endswith(
                "/public/posts/2026/hi"
            )
        )
        # ...and NOT as bare global names, so they can't shadow a consumer's.
        with self.assertRaises(NoReverseMatch):
            reverse("home")
        with self.assertRaises(NoReverseMatch):
            reverse("post-detail", args=["public", 2026, "hi"])

    def test_martor_endpoints_stay_unnamespaced(self):
        from django.urls import reverse

        # Martor reverses its own routes by bare name internally, so mounting it
        # must not sweep it into the mosaic namespace.
        self.assertTrue(reverse("martor_markdownfy"))


class DefaultNamespaceSeedTest(TestCase):
    def test_public_and_private_seeded_by_migration(self):
        # The data migration creates these, so a freshly migrated project (and
        # this test DB) has them without anyone making them by hand.
        names = set(
            Namespace.objects.filter(name__in=["public", "private"]).values_list(
                "name", flat=True
            )
        )
        self.assertEqual(names, {"public", "private"})

    def test_home_renders_on_a_fresh_project(self):
        # Nothing created a namespace in this test; "/" must still 200 rather
        # than 404 because "public" was seeded.
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)


class ViewScopingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public = Namespace.objects.get_or_create(name="public")[0]
        cls.private = Namespace.objects.get_or_create(name="private")[0]
        user = User.objects.create_user("vuser")
        cls.author = Author.objects.create(user=user)
        cls.private_post = Post.objects.create(
            author=cls.author,
            title="Secret",
            slug="secret",
            namespace=cls.private,
            content="hidden",
            is_published=True,
        )

    def test_draft_detail_scoped_to_namespace(self):
        # H2/L2: a private post's draft link must not resolve under /public/.
        resp = self.client.get(f"/public/posts/drafts/{self.private_post.secret_id}")
        self.assertEqual(resp.status_code, 404)

    def test_post_detail_wrong_year_404s(self):
        # M6: the year segment must be honored.
        pub = Post.objects.create(
            author=self.author,
            title="Yearly",
            slug="yearly",
            namespace=self.public,
            content="body",
            is_published=True,
        )
        year = pub.published_at.year
        good = self.client.get(f"/public/posts/{year}/yearly")
        self.assertEqual(good.status_code, 200)
        bad = self.client.get(f"/public/posts/{year - 1}/yearly")
        self.assertEqual(bad.status_code, 404)

    def test_unknown_namespace_home_404s(self):
        # M6: /nonexistent/ previously rendered an empty 200.
        resp = self.client.get("/nonexistent/")
        self.assertEqual(resp.status_code, 404)


class AdminPublishTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        cls.user = User.objects.create_superuser("admin2", "a@b.com", "pass")
        cls.author = Author.objects.create(user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def test_save_and_publish_sets_is_published(self):
        # M2: "Save and publish" on a draft must actually publish it.
        import reversion

        with reversion.create_revision():
            post = Post.objects.create(
                author=self.author,
                title="Draft To Publish",
                slug="draft-to-publish",
                namespace=self.ns,
                content="draft body",
                is_published=False,
            )
        data = {
            "author": self.author.pk,
            "title": post.title,
            "content": post.content,
            "namespace": self.ns.pk,
            "slug": post.slug,
            "published_version": "",
            "tags": [],
            "contentimage_set-TOTAL_FORMS": "0",
            "contentimage_set-INITIAL_FORMS": "0",
            "contentimage_set-MIN_NUM_FORMS": "0",
            "contentimage_set-MAX_NUM_FORMS": "1000",
            "_publish": "Save and publish",
        }
        self.client.post(f"/admin/django_mosaic/post/{post.pk}/change/", data)
        post.refresh_from_db()
        self.assertTrue(post.is_published)
        self.assertIsNotNone(post.published_at)
        self.assertIsNotNone(post.published_version_id)
