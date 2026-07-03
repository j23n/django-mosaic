from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.test import TestCase

from django_mosaic.admin import ContentImageInlineAdmin
from django_mosaic.feeds import PostFeed
from django_mosaic.models import Author, ContentImage, Namespace, Post


class ContentImageMarkdownEscapingTest(TestCase):
    """ContentImage.markdown() must escape user-controlled alt/caption values."""

    def _make_image(self, alt="", caption=""):
        """Build a ContentImage with mocked image fields (no actual files)."""
        img = ContentImage(alt=alt, caption=caption)
        img.image = MagicMock()
        img.image.url = "/media/test.png"
        img.thumb = MagicMock()
        img.thumb.url = "/media/test_thumb.png"
        return img

    def test_alt_single_quote_breakout_is_escaped(self):
        img = self._make_image(alt="' onerror='alert(1)")
        html = img.markdown()
        # The single quotes must be escaped so the attacker can't break out
        # of the alt attribute.
        self.assertNotIn("alt='' onerror=", html)
        self.assertIn("&#x27;", html)

    def test_caption_script_tag_is_escaped(self):
        img = self._make_image(alt="ok", caption="<script>alert('xss')</script>")
        html = img.markdown()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_alt_double_quote_breakout_is_escaped(self):
        img = self._make_image(alt='" onload="alert(1)')
        html = img.markdown()
        # The double quotes must be escaped so the attacker can't inject
        # new attributes.
        self.assertNotIn('alt="" onload=', html)
        self.assertIn("&quot;", html)

    def test_clean_values_pass_through(self):
        img = self._make_image(alt="A sunset", caption="Beautiful view")
        html = img.markdown()
        self.assertIn("A sunset", html)
        self.assertIn("Beautiful view", html)
        self.assertIn("<figure>", html)

    def test_no_caption_omits_figure(self):
        img = self._make_image(alt="A sunset", caption="")
        html = img.markdown()
        self.assertNotIn("<figure>", html)
        self.assertIn("A sunset", html)


class FeedSanitizationTest(TestCase):
    """PostFeed.item_description must sanitize content, not return raw markdown."""

    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="blog")
        user = User.objects.create_user("feeduser")
        cls.author = Author.objects.create(user=user, h_card={})

    def test_script_tags_are_stripped(self):
        post = Post.objects.create(
            author=self.author,
            title="Script Post",
            content="Hello <script>alert('xss')</script> world",
            namespace=self.ns,
            is_published=True,
        )
        feed = PostFeed()
        description = feed.item_description(post)
        self.assertNotIn("<script>", description)
        self.assertIn("Hello", description)

    def test_markdown_is_rendered_to_html(self):
        post = Post.objects.create(
            author=self.author,
            title="Markdown Post",
            content="**bold** and *italic*",
            namespace=self.ns,
            is_published=True,
        )
        feed = PostFeed()
        description = feed.item_description(post)
        self.assertIn("<strong>bold</strong>", description)
        self.assertIn("<em>italic</em>", description)

    def test_onclick_attribute_is_stripped(self):
        post = Post.objects.create(
            author=self.author,
            title="Event Handler Post",
            content='<div onclick="alert(1)">click me</div>',
            namespace=self.ns,
            is_published=True,
        )
        feed = PostFeed()
        description = feed.item_description(post)
        self.assertNotIn("onclick", description)


class CopyMarkdownButtonDoubleEncodingTest(TestCase):
    """The admin copy-markdown button must not double-encode quotes."""

    def test_quotes_in_alt_not_double_encoded(self):
        img = ContentImage(alt='a "quoted" value', caption="")
        img.pk = 1
        img.image = MagicMock()
        img.image.url = "/media/test.png"
        img.thumb = MagicMock()
        img.thumb.url = "/media/test_thumb.png"

        inline = ContentImageInlineAdmin(ContentImage, MagicMock())
        html = inline.copy_markdown_button(img)

        # format_html escapes " to &quot; in the data-markdown attribute.
        # The old code did .replace('"', '&quot;') on top of that, producing
        # &amp;quot; — verify that double-encoding does NOT appear.
        self.assertNotIn("&amp;quot;", html)
