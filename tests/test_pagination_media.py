"""Tests for list pagination and orphaned-media cleanup."""

import io

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from django_mosaic.models import Author, ContentImage, Namespace, Post


def make_upload(name="p.png"):
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), (10, 20, 30)).save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/png")


class PaginationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("pageuser")
        cls.author = Author.objects.create(user=user)
        for i in range(25):
            Post.objects.create(
                author=cls.author,
                title=f"Post {i:02d}",
                slug=f"post-{i:02d}",
                content="body",
                namespace=cls.ns,
                is_published=True,
            )

    @override_settings(MOSAIC_PAGE_SIZE=10)
    def test_home_first_page_limits_and_shows_controls(self):
        resp = self.client.get("/")
        self.assertEqual(len(resp.context["posts"]), 10)
        self.assertContains(resp, "pagination")
        self.assertContains(resp, "Page 1 of 3")

    @override_settings(MOSAIC_PAGE_SIZE=10)
    def test_second_page(self):
        resp = self.client.get("/?page=2")
        self.assertEqual(resp.context["page_obj"].number, 2)
        self.assertEqual(len(resp.context["posts"]), 10)

    @override_settings(MOSAIC_PAGE_SIZE=10)
    def test_last_page_partial(self):
        resp = self.client.get("/?page=3")
        self.assertEqual(len(resp.context["posts"]), 5)

    @override_settings(MOSAIC_PAGE_SIZE=10)
    def test_out_of_range_page_clamps_to_last(self):
        # Paginator.get_page returns the last page for too-high numbers.
        resp = self.client.get("/?page=999")
        self.assertEqual(resp.context["page_obj"].number, 3)

    @override_settings(MOSAIC_PAGE_SIZE=10)
    def test_invalid_page_falls_back_to_first(self):
        resp = self.client.get("/?page=notanumber")
        self.assertEqual(resp.context["page_obj"].number, 1)

    def test_post_list_paginated(self):
        with override_settings(MOSAIC_PAGE_SIZE=5):
            resp = self.client.get("/public/posts")
        self.assertEqual(len(resp.context["posts"]), 5)
        self.assertContains(resp, "Page 1 of 5")

    def test_single_page_hides_controls(self):
        with override_settings(MOSAIC_PAGE_SIZE=100):
            resp = self.client.get("/")
        self.assertNotContains(resp, 'aria-label="Pagination"')

    @override_settings(MOSAIC_PAGE_SIZE=10)
    def test_tags_reflect_whole_namespace_not_just_page(self):
        # A tag on a post that isn't on page 1 must still appear in the
        # topic list (tags come from the full queryset).
        tagged = Post.objects.get(slug="post-24")
        resp_before = self.client.get("/")
        self.assertNotContains(resp_before, "only-on-last")
        tagged.tags.create(name="only-on-last", namespace=self.ns)
        resp = self.client.get("/")
        self.assertContains(resp, "only-on-last")


@override_settings(MEDIA_ROOT="/tmp/mosaic-media-test")
class OrphanedMediaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("mediauser")
        cls.author = Author.objects.create(user=user)

    def _make_post_with_image(self):
        post = Post.objects.create(
            author=self.author,
            title="With image",
            slug="with-image",
            content="body",
            namespace=self.ns,
            is_published=True,
        )
        ci = ContentImage.objects.create(post=post, image=make_upload(), alt="x")
        return post, ci

    def test_deleting_contentimage_removes_files(self):
        _, ci = self._make_post_with_image()
        image_name, thumb_name = ci.image.name, ci.thumb.name
        self.assertTrue(default_storage.exists(image_name))
        self.assertTrue(default_storage.exists(thumb_name))

        ci.delete()

        self.assertFalse(default_storage.exists(image_name))
        self.assertFalse(default_storage.exists(thumb_name))

    def test_deleting_post_cascades_and_cleans_files(self):
        post, ci = self._make_post_with_image()
        image_name = ci.image.name

        post.delete()

        self.assertEqual(ContentImage.objects.count(), 0)
        self.assertFalse(default_storage.exists(image_name))
