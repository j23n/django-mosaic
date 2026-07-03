import io
import re

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from django_mosaic.models import Author, ContentImage, Namespace, Post, Tag


def has_class_tokens(content, *tokens):
    """True if some class="..." attribute contains all given tokens."""
    for match in re.finditer(r'class="([^"]*)"', content):
        classes = set(match.group(1).split())
        if all(t in classes for t in tokens):
            return True
    return False


class HEntryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(user=user, display_name="Test Author")
        cls.tag = Tag.objects.create(name="indieweb", namespace=cls.ns)
        cls.post = Post.objects.create(
            author=cls.author,
            title="Microformats Post",
            slug="microformats-post",
            content="Some content.",
            summary="A summary.",
            namespace=cls.ns,
            is_published=True,
        )
        cls.post.tags.add(cls.tag)

    @classmethod
    def _make_image(cls):
        img_io = io.BytesIO()
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(img_io, format="PNG")
        img_io.seek(0)
        return SimpleUploadedFile("photo.png", img_io.read(), content_type="image/png")


class PostDetailHEntryTest(HEntryTestBase):
    def setUp(self):
        self.resp = self.client.get(self.post.get_absolute_url())
        self.content = self.resp.content.decode()

    def test_u_url_permalink(self):
        self.assertIn('class="u-url"', self.content)
        self.assertIn(self.post.get_absolute_url(), self.content)

    def test_p_category_on_tags(self):
        self.assertTrue(has_class_tokens(self.content, "p-category"))
        self.assertIn("indieweb", self.content)

    def test_dt_published_iso_format(self):
        match = re.search(r'class="dt-published" datetime="([^"]+)"', self.content)
        self.assertIsNotNone(match)
        dt_value = match.group(1)
        # ISO 8601 has a T separator and timezone offset (e.g. +00:00)
        self.assertIn("T", dt_value)
        self.assertRegex(dt_value, r"[+-]\d{2}:\d{2}$")

    def test_no_u_photo_without_image(self):
        self.assertNotIn("u-photo", self.content)


class PostDetailUPhotoTest(HEntryTestBase):
    def test_u_photo_with_content_image(self):
        ContentImage.objects.create(
            post=self.post, image=self._make_image(), alt="Test photo"
        )
        resp = self.client.get(self.post.get_absolute_url())
        content = resp.content.decode()
        self.assertIn('class="u-photo"', content)
        self.assertIn('alt="Test photo"', content)

    def test_u_photo_uses_featured_image(self):
        ContentImage.objects.create(
            post=self.post, image=self._make_image(), alt="Regular photo"
        )
        ContentImage.objects.create(
            post=self.post,
            image=self._make_image(),
            alt="Featured photo",
            is_featured=True,
        )
        resp = self.client.get(self.post.get_absolute_url())
        content = resp.content.decode()
        self.assertIn('class="u-photo"', content)
        self.assertIn('alt="Featured photo"', content)

    def test_u_photo_fallback_to_first_image(self):
        ContentImage.objects.create(
            post=self.post, image=self._make_image(), alt="First photo"
        )
        ContentImage.objects.create(
            post=self.post, image=self._make_image(), alt="Second photo"
        )
        resp = self.client.get(self.post.get_absolute_url())
        content = resp.content.decode()
        self.assertIn('alt="First photo"', content)


class HFeedTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
        user = User.objects.create_user("feeduser")
        cls.author = Author.objects.create(user=user, display_name="Feed Author")
        cls.post = Post.objects.create(
            author=cls.author,
            title="Feed Post",
            slug="feed-post",
            content="Content.",
            namespace=cls.ns,
            is_published=True,
        )


class HomepageHFeedTest(HFeedTestBase):
    def setUp(self):
        self.resp = self.client.get("/")
        self.content = self.resp.content.decode()

    def test_h_feed_container(self):
        self.assertIn('class="h-feed"', self.content)

    def test_h_entry_on_list_item(self):
        self.assertTrue(has_class_tokens(self.content, "h-entry"))

    def test_u_url_on_link(self):
        self.assertTrue(has_class_tokens(self.content, "u-url"))

    def test_p_name_on_title(self):
        self.assertIn('class="p-name"', self.content)
        self.assertIn("Feed Post", self.content)

    def test_dt_published_on_date(self):
        match = re.search(r'class="dt-published" datetime="([^"]+)"', self.content)
        self.assertIsNotNone(match)
        self.assertIn("T", match.group(1))


class PostListHFeedTest(HFeedTestBase):
    def test_h_feed_on_post_list(self):
        resp = self.client.get("/public/posts")
        self.assertContains(resp, 'class="h-feed"')
        self.assertTrue(has_class_tokens(resp.content.decode(), "h-entry"))


class TagDetailHFeedTest(HFeedTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tag = Tag.objects.create(name="test-tag", namespace=cls.ns)
        cls.post.tags.add(cls.tag)

    def test_h_feed_on_tag_page(self):
        resp = self.client.get(self.tag.get_absolute_url())
        self.assertContains(resp, 'class="h-feed"')
        self.assertTrue(has_class_tokens(resp.content.decode(), "h-entry"))
