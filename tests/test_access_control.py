from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from django_mosaic.models import Author, Namespace, Post


class AccessControlTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public_ns = Namespace.objects.get_or_create(name="public")[0]
        cls.private_ns = Namespace.objects.get_or_create(name="private")[0]

        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(user=user, h_card={})

        cls.published_post = Post.objects.create(
            author=cls.author,
            title="Published Post",
            slug="published-post",
            content="Hello world",
            namespace=cls.public_ns,
            is_published=True,
        )
        cls.draft_post = Post.objects.create(
            author=cls.author,
            title="Draft Post",
            slug="draft-post",
            content="Secret draft",
            namespace=cls.public_ns,
            is_published=False,
        )
        cls.private_post = Post.objects.create(
            author=cls.author,
            title="Private Post",
            slug="private-post",
            content="Private content",
            namespace=cls.private_ns,
            is_published=True,
        )


class FeedAccessControlTest(AccessControlTestBase):
    def test_feed_excludes_unpublished_posts(self):
        resp = self.client.get("/public/feed")
        self.assertContains(resp, "Published Post")
        self.assertNotContains(resp, "Draft Post")

    def test_feed_excludes_other_namespace(self):
        resp = self.client.get("/public/feed")
        self.assertNotContains(resp, "Private Post")


class PostDetailAccessControlTest(AccessControlTestBase):
    def test_published_post_accessible(self):
        year = self.published_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/published-post")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Published Post")

    def test_unpublished_post_returns_404(self):
        resp = self.client.get("/public/posts/2026/draft-post")
        self.assertEqual(resp.status_code, 404)

    def test_private_post_not_accessible_via_public_url(self):
        year = self.private_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/private-post")
        self.assertEqual(resp.status_code, 404)

    def test_private_post_requires_auth_via_own_namespace(self):
        year = self.private_post.published_at.year
        resp = self.client.get(f"/private/posts/{year}/private-post")
        # protected_path requires authentication -- unauthenticated gets 403
        self.assertEqual(resp.status_code, 403)

    def test_case_variant_private_namespace_does_not_bypass_gate(self):
        # The token gate matches the "private/" path prefix case-sensitively.
        # A case-insensitive DB collation must not let /PRIVATE/... resolve the
        # gated namespace and serve its posts unauthenticated: the view
        # requires an exact-case namespace match, so these 404 (never 200).
        year = self.private_post.published_at.year
        for url in (
            f"/PRIVATE/posts/{year}/private-post",
            "/PRIVATE/",
            "/PRIVATE/posts",
            "/PRIVATE/feed",
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 404, url)


class PrevNextNavigationTest(AccessControlTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        cls.older_post = Post.objects.create(
            author=cls.author,
            title="Older Post",
            slug="older-post",
            content="Older",
            namespace=cls.public_ns,
            is_published=True,
        )
        # Force an older published_at
        Post.objects.filter(pk=cls.older_post.pk).update(
            published_at=cls.published_post.published_at - timezone.timedelta(days=2)
        )
        cls.older_post.refresh_from_db()

        # Unpublished post sitting between the two published ones
        cls.middle_draft = Post.objects.create(
            author=cls.author,
            title="Middle Draft",
            slug="middle-draft",
            content="Draft between published posts",
            namespace=cls.public_ns,
            is_published=False,
        )
        # Give it a published_at between the two published posts so it would
        # appear in navigation if is_published wasn't checked
        Post.objects.filter(pk=cls.middle_draft.pk).update(
            published_at=cls.published_post.published_at - timezone.timedelta(days=1)
        )

    def test_prev_next_skips_unpublished(self):
        year = self.published_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/published-post")
        self.assertEqual(resp.status_code, 200)

        prev_post = resp.context["prev_post"]
        self.assertIsNotNone(prev_post)
        self.assertEqual(prev_post.title, "Older Post")
        # Middle Draft should not appear as prev_post

    def test_prev_next_skips_other_namespace(self):
        year = self.older_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/older-post")
        next_post = resp.context["next_post"]
        self.assertIsNotNone(next_post)
        self.assertEqual(next_post.title, "Published Post")
        # Private Post should not appear
