"""Tests for the martor editor integration and the admin image uploads."""

import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from django_mosaic.models import Author, ContentImage, Namespace, Post

UPLOAD_URL = "/admin/django_mosaic/post/upload-image/"


def make_upload(name="pic.png", mode="RGB", size=(80, 60)):
    buf = io.BytesIO()
    Image.new(mode, size, color=(30, 60, 90)).save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/png")


class UploadTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ns = Namespace.objects.create(name="public")
        cls.admin = User.objects.create_superuser("boss", "b@x.com", "pass")
        cls.author = Author.objects.create(user=cls.admin)
        cls.post = Post.objects.create(
            author=cls.author,
            title="Editor Post",
            slug="editor-post",
            namespace=cls.ns,
            content="hello",
        )

    def setUp(self):
        self.client.force_login(self.admin)


class MartorWidgetTest(UploadTestBase):
    def test_admin_change_form_uses_martor(self):
        resp = self.client.get(f"/admin/django_mosaic/post/{self.post.pk}/change/")
        self.assertContains(resp, "martor")

    def test_dropzone_rendered_on_change_form(self):
        resp = self.client.get(f"/admin/django_mosaic/post/{self.post.pk}/change/")
        self.assertContains(resp, "mosaic-dropzone")

    def test_dropzone_absent_on_add_form(self):
        resp = self.client.get("/admin/django_mosaic/post/add/")
        self.assertNotContains(resp, "mosaic-dropzone")

    def test_markdownify_preview_endpoint(self):
        resp = self.client.post("/martor/markdownify/", {"content": "**hi**"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"<strong>hi</strong>", resp.content)


class UploadEndpointTest(UploadTestBase):
    def test_requires_staff(self):
        self.client.logout()
        resp = self.client.post(UPLOAD_URL, {"image": make_upload()})
        # admin_view redirects anonymous users to the login page.
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ContentImage.objects.count(), 0)

    def test_dropzone_upload_attaches_to_post(self):
        resp = self.client.post(
            UPLOAD_URL, {"image": make_upload(), "post_id": self.post.pk}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], 200)
        self.assertTrue(data["link"])
        ci = ContentImage.objects.get()
        self.assertEqual(ci.post, self.post)
        self.assertTrue(ci.thumb, "upload must be processed and thumbnailed")

    def test_martor_field_name_and_referer_attachment(self):
        resp = self.client.post(
            UPLOAD_URL,
            {"markdown-image-upload": make_upload()},
            HTTP_REFERER=f"http://testserver/admin/django_mosaic/post/{self.post.pk}/change/",
        )
        self.assertEqual(resp.json()["status"], 200)
        self.assertEqual(ContentImage.objects.get().post, self.post)

    def test_upload_without_post_is_unattached(self):
        resp = self.client.post(UPLOAD_URL, {"markdown-image-upload": make_upload()})
        self.assertEqual(resp.json()["status"], 200)
        self.assertIsNone(ContentImage.objects.get().post)

    def test_non_image_rejected(self):
        bogus = SimpleUploadedFile(
            "evil.png", b"not an image at all", content_type="image/png"
        )
        resp = self.client.post(UPLOAD_URL, {"image": bogus})
        self.assertEqual(resp.json()["status"], 400)
        self.assertEqual(ContentImage.objects.count(), 0)

    def test_get_not_allowed(self):
        resp = self.client.get(UPLOAD_URL)
        self.assertEqual(resp.status_code, 405)
