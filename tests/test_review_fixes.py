"""Regression tests for issues found in the code review."""

import io

from django.test import TestCase
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from PIL import Image

from django_mosaic.models import Author, ContentImage, Namespace, Post, Tag


class SlugGenerationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
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
        cls.ns = Namespace.objects.create(name="public")
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
        cls.ns = Namespace.objects.create(name="public")

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
        cls.ns = Namespace.objects.create(name="public")
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


class ViewScopingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public = Namespace.objects.create(name="public")
        cls.private = Namespace.objects.create(name="private")
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
        cls.ns = Namespace.objects.create(name="public")
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
