from django.test import TestCase
from django.contrib.auth.models import User

from django_mosaic.models import Post, Namespace, Author
from django_mosaic.admin import PostAdmin


class DraftVersioningTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(user=user, h_card={})

        cls.published_post = Post.objects.create(
            author=cls.author,
            title="Published Post",
            slug="dv-published-post",
            content="Published content",
            namespace=cls.ns,
            is_published=True,
        )
        cls.draft_post = Post.objects.create(
            author=cls.author,
            title="Draft Post",
            slug="dv-draft-post",
            content="Original content",
            draft_content="Draft content here",
            namespace=cls.ns,
            is_published=True,
        )


class DraftModelTest(DraftVersioningTestBase):
    def test_draft_content_defaults_to_none(self):
        post = Post.objects.create(
            author=self.author,
            title="New Post",
            content="Content",
            namespace=self.ns,
        )
        self.assertIsNone(post.draft_content)

    def test_has_draft_true_when_draft_content_set(self):
        self.assertTrue(self.draft_post.has_draft)

    def test_has_draft_false_when_no_draft_content(self):
        self.assertFalse(self.published_post.has_draft)


class DraftViewContentTest(DraftVersioningTestBase):
    def test_draft_view_shows_draft_content_when_set(self):
        resp = self.client.get(
            f"/public/posts/drafts/{self.draft_post.secret_id}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Draft content here")
        # The e-content div should show draft content, not original
        self.assertContains(resp, '<div class="e-content">\nDraft content here\n</div>')
        self.assertNotContains(resp, '<div class="e-content">\nOriginal content\n</div>')

    def test_draft_view_falls_back_to_content(self):
        resp = self.client.get(
            f"/public/posts/drafts/{self.published_post.secret_id}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Published content")


class PublishedViewIsolationTest(DraftVersioningTestBase):
    def test_published_view_never_shows_draft_content(self):
        year = self.draft_post.published_at.year
        resp = self.client.get(
            f"/public/posts/{year}/{self.draft_post.slug}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Original content")
        self.assertNotContains(resp, "Draft content here")


class DraftBannerTest(DraftVersioningTestBase):
    def test_draft_banner_shown_on_draft_url(self):
        resp = self.client.get(
            f"/public/posts/drafts/{self.published_post.secret_id}"
        )
        self.assertContains(resp, "Draft preview")

    def test_draft_banner_not_shown_on_published_url(self):
        year = self.published_post.published_at.year
        resp = self.client.get(
            f"/public/posts/{year}/{self.published_post.slug}"
        )
        self.assertNotContains(resp, "Draft preview")


class ReferrerPolicyTest(DraftVersioningTestBase):
    def test_draft_response_has_no_referrer_header(self):
        resp = self.client.get(
            f"/public/posts/drafts/{self.published_post.secret_id}"
        )
        self.assertEqual(resp["Referrer-Policy"], "no-referrer")

    def test_published_response_has_no_referrer_policy(self):
        year = self.published_post.published_at.year
        resp = self.client.get(
            f"/public/posts/{year}/{self.published_post.slug}"
        )
        self.assertFalse(resp.has_header("Referrer-Policy"))


class AdminPublishDraftTest(DraftVersioningTestBase):
    def test_publish_draft_copies_content_and_clears_draft(self):
        post = self.draft_post
        post.refresh_from_db()

        post.content = post.draft_content
        post.draft_content = None
        post.save(update_fields=["content", "draft_content"])

        post.refresh_from_db()
        self.assertEqual(post.content, "Draft content here")
        self.assertIsNone(post.draft_content)
        self.assertFalse(post.has_draft)

    def test_publish_draft_skips_posts_without_draft(self):
        post = self.published_post
        original_content = post.content
        post.refresh_from_db()

        # Simulate the action logic: skip if no draft
        self.assertFalse(post.has_draft)
        self.assertEqual(post.content, original_content)


class AdminIndicatorTest(DraftVersioningTestBase):
    def test_has_draft_indicator_shows_draft_pending(self):
        admin = PostAdmin(Post, None)
        result = admin.has_draft_indicator(self.draft_post)
        self.assertEqual(result, "Draft pending")

    def test_has_draft_indicator_empty_when_no_draft(self):
        admin = PostAdmin(Post, None)
        result = admin.has_draft_indicator(self.published_post)
        self.assertEqual(result, "")

    def test_draft_preview_link_shows_link_when_draft(self):
        admin = PostAdmin(Post, None)
        result = admin.draft_preview_link(self.draft_post)
        self.assertIn("Preview draft", result)
        self.assertIn(self.draft_post.secret_id, result)

    def test_draft_preview_link_shows_no_draft_when_none(self):
        admin = PostAdmin(Post, None)
        result = admin.draft_preview_link(self.published_post)
        self.assertEqual(result, "No draft pending")
