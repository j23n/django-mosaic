import reversion
from django.contrib.auth.models import User
from django.test import TestCase
from reversion.models import Version

from django_mosaic.models import Author, Namespace, Post


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
        resp = self.client.get(f"/public/posts/drafts/{self.draft_post.secret_id}")
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
        resp = self.client.get(f"/public/posts/drafts/{post.secret_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fallback content")


class PublishedContentPropertyTest(RevisionTestBase):
    def test_published_content_returns_version_content_when_set(self):
        versions = Version.objects.get_for_object(self.draft_post)
        original = versions.last()
        self.draft_post.published_version_id = original.pk
        self.assertEqual(self.draft_post.published_content, "Original content")

    def test_published_content_falls_back_when_unset(self):
        self.draft_post.published_version_id = None
        self.assertEqual(self.draft_post.published_content, self.draft_post.content)

    def test_published_content_falls_back_when_version_deleted(self):
        self.draft_post.published_version_id = 999999
        self.assertEqual(self.draft_post.published_content, self.draft_post.content)

    def test_deleting_pinned_version_nulls_the_pointer(self):
        # The FK's on_delete=SET_NULL must clear the pointer instead of
        # leaving a dangling id when a pinned Version is deleted.
        versions = Version.objects.get_for_object(self.draft_post)
        original = versions.last()
        self.draft_post.published_version_id = original.pk
        self.draft_post.save(update_fields=["published_version"])

        original.delete()

        self.draft_post.refresh_from_db()
        self.assertIsNone(self.draft_post.published_version_id)
        self.assertEqual(self.draft_post.published_content, self.draft_post.content)


class PublishedViewIsolationTest(RevisionTestBase):
    def test_published_view_shows_published_content(self):
        # Pin published_version_id to the original revision
        versions = Version.objects.get_for_object(self.draft_post)
        original = versions.last()
        self.draft_post.published_version_id = original.pk
        self.draft_post.save(update_fields=["published_version"])

        year = self.draft_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/{self.draft_post.slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Original content")
        self.assertNotContains(resp, "Draft content here")

    def test_published_view_shows_current_content_when_no_version_pinned(self):
        # When no version is pinned, published_content falls back to self.content
        self.draft_post.published_version_id = None
        self.draft_post.save(update_fields=["published_version"])

        year = self.draft_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/{self.draft_post.slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Draft content here")

    def test_draft_view_shows_latest_revision_regardless(self):
        # Even with a pinned published version, draft view shows latest
        versions = Version.objects.get_for_object(self.draft_post)
        original = versions.last()
        self.draft_post.published_version_id = original.pk
        self.draft_post.save(update_fields=["published_version"])

        resp = self.client.get(f"/public/posts/drafts/{self.draft_post.secret_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Draft content here")


class DraftBannerTest(RevisionTestBase):
    def test_draft_banner_shown_on_draft_url(self):
        resp = self.client.get(f"/public/posts/drafts/{self.published_post.secret_id}")
        self.assertContains(resp, "Draft preview")

    def test_draft_banner_not_shown_on_published_url(self):
        year = self.published_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/{self.published_post.slug}")
        self.assertNotContains(resp, "Draft preview")


class ReferrerPolicyTest(RevisionTestBase):
    def test_draft_response_has_no_referrer_header(self):
        resp = self.client.get(f"/public/posts/drafts/{self.published_post.secret_id}")
        self.assertEqual(resp["Referrer-Policy"], "no-referrer")

    def test_published_response_has_no_referrer_policy(self):
        year = self.published_post.published_at.year
        resp = self.client.get(f"/public/posts/{year}/{self.published_post.slug}")
        self.assertFalse(resp.has_header("Referrer-Policy"))


class PublishRevisionTest(RevisionTestBase):
    def test_publish_revision_sets_published_version_id(self):
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
        post.published_version_id = latest.pk
        post.is_published = True
        post.save(update_fields=["published_version", "is_published"])

        post.refresh_from_db()
        self.assertEqual(post.published_version_id, latest.pk)
        self.assertEqual(post.published_content, "v2 ready to publish")
        self.assertTrue(post.is_published)


class AdminRevisionCreationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="rev-admin")
        cls.user = User.objects.create_superuser("admin", "admin@test.com", "pass")
        cls.author = Author.objects.create(user=cls.user, h_card={})

    def setUp(self):
        self.client.force_login(self.user)
        with reversion.create_revision():
            self.post = Post.objects.create(
                author=self.author,
                title="Admin Test",
                slug="admin-test",
                content="original",
                namespace=self.ns,
                is_published=True,
            )

    def _post_data(self, **overrides):
        data = {
            "author": self.author.pk,
            "title": self.post.title,
            "content": self.post.content,
            "namespace": self.ns.pk,
            "is_published": "on",
            "slug": self.post.slug,
            "published_version": "",
            "tags": [],
            "contentimage_set-TOTAL_FORMS": "0",
            "contentimage_set-INITIAL_FORMS": "0",
            "contentimage_set-MIN_NUM_FORMS": "0",
            "contentimage_set-MAX_NUM_FORMS": "1000",
            "_save": "Save",
        }
        data.update(overrides)
        return data

    def test_save_without_content_change_creates_no_revision(self):
        count_before = Version.objects.get_for_object(self.post).count()
        self.client.post(
            f"/admin/django_mosaic/post/{self.post.pk}/change/",
            self._post_data(),
        )
        count_after = Version.objects.get_for_object(self.post).count()
        self.assertEqual(count_after, count_before)

    def test_save_with_content_change_creates_revision(self):
        count_before = Version.objects.get_for_object(self.post).count()
        self.client.post(
            f"/admin/django_mosaic/post/{self.post.pk}/change/",
            self._post_data(content="updated content"),
        )
        count_after = Version.objects.get_for_object(self.post).count()
        self.assertEqual(count_after, count_before + 1)
        latest = Version.objects.get_for_object(self.post).first()
        self.assertEqual(latest.field_dict["content"], "updated content")

    def test_save_and_publish_pins_latest_version(self):
        self.client.post(
            f"/admin/django_mosaic/post/{self.post.pk}/change/",
            {
                **self._post_data(content="publish me"),
                "_publish": "Save and publish",
                "_save": "",
            },
        )
        self.post.refresh_from_db()
        latest = Version.objects.get_for_object(self.post).first()
        self.assertEqual(self.post.published_version_id, latest.pk)
        self.assertEqual(self.post.published_content, "publish me")


class FeedPublishedContentTest(RevisionTestBase):
    def test_feed_uses_published_content(self):
        # Pin to original version
        versions = Version.objects.get_for_object(self.draft_post)
        original = versions.last()
        self.draft_post.published_version_id = original.pk
        self.draft_post.save(update_fields=["published_version"])

        resp = self.client.get("/public/feed")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Original content")
