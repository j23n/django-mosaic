import re

from django.contrib.auth.models import User
from django.test import TestCase

from django_mosaic.models import Author, Namespace, Post, RelMeLink


def has_class_tokens(content, *tokens):
    """True if some class="..." attribute contains all given tokens.

    Robust to class ordering and extra cosmetic classes, which is what matters
    for microformats (the tokens carry the semantics, not their order).
    """
    for match in re.finditer(r'class="([^"]*)"', content):
        classes = set(match.group(1).split())
        if all(t in classes for t in tokens):
            return True
    return False


class HCardTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(
            user=user,
            display_name="Jane Doe",
            url="https://jane.example.com",
            email="jane@example.com",
            photo_url="https://jane.example.com/photo.jpg",
            note="Indieweb enthusiast",
        )
        RelMeLink.objects.create(
            author=cls.author, url="https://github.com/janedoe", label="GitHub"
        )
        RelMeLink.objects.create(
            author=cls.author, url="https://mastodon.social/@jane", label=""
        )
        # Need a published post so homepage renders
        cls.post = Post.objects.create(
            author=cls.author,
            title="Test Post",
            slug="test-post",
            content="Hello",
            namespace=cls.ns,
            is_published=True,
        )


class HCardRenderingTest(HCardTestBase):
    def test_hcard_in_footer_on_homepage(self):
        resp = self.client.get("/")
        self.assertTrue(has_class_tokens(resp.content.decode(), "h-card"))

    def test_hcard_in_footer_on_post_page(self):
        resp = self.client.get(self.post.get_absolute_url())
        self.assertTrue(has_class_tokens(resp.content.decode(), "h-card"))

    def test_hcard_microformat_classes(self):
        resp = self.client.get("/")
        content = resp.content.decode()
        self.assertTrue(has_class_tokens(content, "p-name", "u-url"))
        self.assertTrue(has_class_tokens(content, "u-email"))
        self.assertTrue(has_class_tokens(content, "u-photo"))
        self.assertTrue(has_class_tokens(content, "p-note"))

    def test_hcard_field_values(self):
        resp = self.client.get("/")
        content = resp.content.decode()
        self.assertIn("Jane Doe", content)
        self.assertIn("https://jane.example.com", content)
        self.assertIn("jane@example.com", content)
        self.assertIn("https://jane.example.com/photo.jpg", content)
        self.assertIn("Indieweb enthusiast", content)

    def test_rel_me_links_in_hcard(self):
        resp = self.client.get("/")
        content = resp.content.decode()
        self.assertIn('rel="me"', content)
        self.assertIn("https://github.com/janedoe", content)
        self.assertIn("GitHub", content)
        self.assertIn("https://mastodon.social/@jane", content)

    def test_rel_me_links_separated_by_middot(self):
        resp = self.client.get("/")
        self.assertContains(resp, "·")

    def test_rel_me_link_tags_in_head(self):
        """rel=me <link> tags appear in <head> on all pages."""
        resp = self.client.get("/")
        content = resp.content.decode()
        self.assertIn('<link rel="me" href="https://github.com/janedoe">', content)
        self.assertIn('<link rel="me" href="https://mastodon.social/@jane">', content)

    def test_rel_me_in_head_on_post_page(self):
        resp = self.client.get(self.post.get_absolute_url())
        content = resp.content.decode()
        self.assertIn('<link rel="me" href="https://github.com/janedoe">', content)


class HCardBlankFieldsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        ns = Namespace.objects.get_or_create(name="public")[0]
        user = User.objects.create_user("minimal")
        cls.author = Author.objects.create(
            user=user,
            display_name="Minimal User",
        )
        Post.objects.create(
            author=cls.author,
            title="Post",
            slug="minimal-post",
            content="Content",
            namespace=ns,
            is_published=True,
        )

    def test_blank_fields_omitted(self):
        resp = self.client.get("/")
        content = resp.content.decode()
        self.assertTrue(has_class_tokens(content, "h-card"))
        self.assertIn("Minimal User", content)
        self.assertNotIn("u-email", content)
        self.assertNotIn("u-photo", content)
        self.assertNotIn("p-note", content)
        self.assertNotIn('rel="me"', content)


class HCardNoAuthorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Namespace.objects.get_or_create(name="public")[0]

    def test_no_author_graceful(self):
        """Homepage renders without error when no Author exists."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'class="h-card"')
