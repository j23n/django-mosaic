import reversion
from reversion.models import Version

from django.test import TestCase
from django.contrib.auth.models import User

from django_mosaic.models import Post, Namespace, Author


class RevisionTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(user=user, h_card={})

        with reversion.create_revision():
            cls.published_post = Post.objects.create(
                author=cls.author,
                title="Published Post",
                slug="dv-published-post",
                content="Published content",
                namespace=cls.ns,
                is_published=True,
            )

        with reversion.create_revision():
            cls.draft_post = Post.objects.create(
                author=cls.author,
                title="Draft Post",
                slug="dv-draft-post",
                content="Original content",
                namespace=cls.ns,
                is_published=True,
            )
        # Create a second revision with updated content (the "draft")
        with reversion.create_revision():
            cls.draft_post.content = "Draft content here"
            cls.draft_post.save()


class RevisionModelTest(RevisionTestBase):
    def test_revisions_are_created(self):
        versions = Version.objects.get_for_object(self.draft_post)
        self.assertEqual(versions.count(), 2)

    def test_latest_revision_has_updated_content(self):
        latest = Version.objects.get_for_object(self.draft_post).first()
        self.assertEqual(latest.field_dict["content"], "Draft content here")

    def test_original_revision_preserved(self):
        versions = Version.objects.get_for_object(self.draft_post)
        original = versions.last()
        self.assertEqual(original.field_dict["content"], "Original content")


class DraftViewContentTest(RevisionTestBase):
    def test_draft_view_shows_latest_revision_content(self):
        resp = self.client.get(
            f"/public/posts/drafts/{self.draft_post.secret_id}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Draft content here")

    def test_draft_view_falls_back_to_current_content(self):
        """When no revisions exist, the view shows current content."""
        post = Post.objects.create(
            author=self.author,
            title="No Revisions",
            slug="no-revisions",
            content="Fallback content",
            namespace=self.ns,
            is_published=True,
        )
        resp = self.client.get(
            f"/public/posts/drafts/{post.secret_id}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fallback content")


class PublishedViewIsolationTest(RevisionTestBase):
    def test_published_view_shows_saved_content(self):
        # The published view shows post.content (which was updated in the
        # second revision), not any specific revision. This verifies the
        # published view works independently of reversion.
        year = self.draft_post.published_at.year
        resp = self.client.get(
            f"/public/posts/{year}/{self.draft_post.slug}"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Draft content here")


class DraftBannerTest(RevisionTestBase):
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


class ReferrerPolicyTest(RevisionTestBase):
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


class PublishRevisionTest(RevisionTestBase):
    def test_publish_revision_updates_content(self):
        """Simulate the admin action: publish latest revision."""
        post = Post.objects.create(
            author=self.author,
            title="Unpublished",
            slug="unpublished",
            content="v1",
            namespace=self.ns,
            is_published=False,
        )
        with reversion.create_revision():
            post.content = "v2 ready to publish"
            post.save()

        # Simulate admin action logic
        versions = Version.objects.get_for_object(post)
        latest = versions.first()
        post.content = latest.field_dict["content"]
        post.is_published = True
        post.save(update_fields=["content", "is_published"])

        post.refresh_from_db()
        self.assertEqual(post.content, "v2 ready to publish")
        self.assertTrue(post.is_published)
