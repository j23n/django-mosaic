from django.contrib.auth.models import User
from django.test import TestCase

from django_mosaic.models import Author, Namespace, Post


class PostIntegrityTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="test")
        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(user=user, h_card={})

    def _make_post(self, **kwargs):
        defaults = {
            "author": self.author,
            "content": "Some content",
            "namespace": self.ns,
        }
        defaults.update(kwargs)
        return Post.objects.create(**defaults)


class SlugCollisionTest(PostIntegrityTestBase):
    def test_two_posts_same_title_get_distinct_slugs(self):
        p1 = self._make_post(title="Hello World")
        p2 = self._make_post(title="Hello World")
        self.assertEqual(p1.slug, "hello-world")
        self.assertEqual(p2.slug, "hello-world-2")

    def test_slug_truncation_fits_in_max_length(self):
        long_title = "a" * 500
        p = self._make_post(title=long_title)
        self.assertLessEqual(len(p.slug), 256)

    def test_slug_truncation_with_collision_fits_in_max_length(self):
        long_title = "a" * 500
        p1 = self._make_post(title=long_title)
        p2 = self._make_post(title=long_title)
        self.assertLessEqual(len(p2.slug), 256)
        self.assertNotEqual(p1.slug, p2.slug)


class UpdateFieldsTest(PostIntegrityTestBase):
    def test_save_update_fields_persists_published_at(self):
        post = self._make_post(title="Test UF", is_published=False)
        self.assertIsNone(post.published_at)

        post.is_published = True
        post.save(update_fields=["is_published"])

        post.refresh_from_db()
        self.assertIsNotNone(post.published_at)


class PublishedAtPreservationTest(PostIntegrityTestBase):
    def test_unpublish_preserves_published_at(self):
        post = self._make_post(title="Preserve Test", is_published=True)
        original_published_at = post.published_at
        self.assertIsNotNone(original_published_at)

        post.is_published = False
        post.save()

        post.refresh_from_db()
        self.assertEqual(post.published_at, original_published_at)

    def test_republish_keeps_original_published_at(self):
        post = self._make_post(title="Republish Test", is_published=True)
        original_published_at = post.published_at
        self.assertIsNotNone(original_published_at)

        # Unpublish
        post.is_published = False
        post.save()

        # Republish
        post.is_published = True
        post.save()

        post.refresh_from_db()
        self.assertEqual(post.published_at, original_published_at)


class SecretIdUniqueTest(PostIntegrityTestBase):
    def test_secret_id_field_has_unique_constraint(self):
        field = Post._meta.get_field("secret_id")
        self.assertTrue(field.unique)
