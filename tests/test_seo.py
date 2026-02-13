from django.test import TestCase
from django.contrib.auth.models import User

from django_mosaic.models import Author, ContentImage, Namespace, Post


class SEOTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public_ns = Namespace.objects.create(name="public")
        cls.private_ns = Namespace.objects.create(name="private")
        user = User.objects.create_user("testuser")
        cls.author = Author.objects.create(user=user, display_name="Test Author")
        cls.post = Post.objects.create(
            author=cls.author,
            title="SEO Test Post",
            slug="seo-test-post",
            content="Full content here.",
            summary="A brief summary for SEO.",
            namespace=cls.public_ns,
            is_published=True,
        )
        cls.unpublished_post = Post.objects.create(
            author=cls.author,
            title="Draft Post",
            slug="draft-post",
            content="Not published.",
            namespace=cls.public_ns,
            is_published=False,
        )
        cls.private_post = Post.objects.create(
            author=cls.author,
            title="Private Post",
            slug="private-post",
            content="Private content.",
            namespace=cls.private_ns,
            is_published=True,
        )


class PostDetailMetaTest(SEOTestBase):
    def setUp(self):
        self.resp = self.client.get(self.post.get_absolute_url())

    def test_meta_description_has_summary(self):
        self.assertContains(
            self.resp,
            '<meta name="description" content="A brief summary for SEO.">',
        )

    def test_og_title(self):
        self.assertContains(
            self.resp,
            '<meta property="og:title" content="SEO Test Post">',
        )

    def test_og_description(self):
        self.assertContains(
            self.resp,
            '<meta property="og:description" content="A brief summary for SEO.">',
        )

    def test_og_url(self):
        self.assertContains(self.resp, '<meta property="og:url"')
        self.assertContains(self.resp, self.post.get_absolute_url())

    def test_og_type_article(self):
        self.assertContains(
            self.resp,
            '<meta property="og:type" content="article">',
        )

    def test_canonical_url(self):
        self.assertContains(self.resp, '<link rel="canonical"')
        self.assertContains(self.resp, self.post.get_absolute_url())


class PostDetailLightboxTest(SEOTestBase):
    def setUp(self):
        self.resp = self.client.get(self.post.get_absolute_url())

    def test_glightbox_css_loaded(self):
        self.assertContains(self.resp, "glightbox.min.css")

    def test_glightbox_js_loaded(self):
        self.assertContains(self.resp, "glightbox.min.js")

    def test_glightbox_targets_content_links(self):
        self.assertContains(self.resp, "selector: '.e-content a'")


class PostDetailTwitterCardTest(SEOTestBase):
    def setUp(self):
        self.resp = self.client.get(self.post.get_absolute_url())

    def test_twitter_card_summary(self):
        self.assertContains(
            self.resp,
            '<meta name="twitter:card" content="summary">',
        )

    def test_twitter_title(self):
        self.assertContains(
            self.resp,
            '<meta name="twitter:title" content="SEO Test Post">',
        )

    def test_twitter_description(self):
        self.assertContains(
            self.resp,
            '<meta name="twitter:description" content="A brief summary for SEO.">',
        )


class PostDetailWithImageTest(SEOTestBase):
    @classmethod
    def _create_image(cls):
        from django.core.files.uploadedfile import SimpleUploadedFile
        import io
        from PIL import Image

        img_io = io.BytesIO()
        img = Image.new("RGB", (100, 100), color="red")
        img.save(img_io, format="PNG")
        img_io.seek(0)
        return SimpleUploadedFile("test.png", img_io.read(), content_type="image/png")

    def test_og_image_present(self):
        ContentImage.objects.create(post=self.post, image=self._create_image())
        resp = self.client.get(self.post.get_absolute_url())
        self.assertContains(resp, '<meta property="og:image"')

    def test_twitter_large_image_card(self):
        ContentImage.objects.create(post=self.post, image=self._create_image())
        resp = self.client.get(self.post.get_absolute_url())
        self.assertContains(
            resp,
            '<meta name="twitter:card" content="summary_large_image">',
        )

    def test_twitter_image_present(self):
        ContentImage.objects.create(post=self.post, image=self._create_image())
        resp = self.client.get(self.post.get_absolute_url())
        self.assertContains(resp, '<meta name="twitter:image"')

    def test_featured_image_used_for_og_image(self):
        ContentImage.objects.create(
            post=self.post, image=self._create_image(), alt="regular"
        )
        featured = ContentImage.objects.create(
            post=self.post, image=self._create_image(), alt="featured", is_featured=True
        )
        resp = self.client.get(self.post.get_absolute_url())
        self.assertContains(resp, featured.image.url)

    def test_featured_image_used_for_twitter_image(self):
        ContentImage.objects.create(
            post=self.post, image=self._create_image(), alt="regular"
        )
        featured = ContentImage.objects.create(
            post=self.post, image=self._create_image(), alt="featured", is_featured=True
        )
        resp = self.client.get(self.post.get_absolute_url())
        content = resp.content.decode()
        self.assertIn(featured.image.url, content)

    def test_fallback_to_first_image_when_no_featured(self):
        first = ContentImage.objects.create(
            post=self.post, image=self._create_image(), alt="first"
        )
        ContentImage.objects.create(
            post=self.post, image=self._create_image(), alt="second"
        )
        resp = self.client.get(self.post.get_absolute_url())
        self.assertContains(resp, first.image.url)


class HomepageMetaTest(SEOTestBase):
    def setUp(self):
        self.resp = self.client.get("/")

    def test_meta_description_from_constants(self):
        self.assertContains(
            self.resp,
            '<meta name="description" content="A test blog.">',
        )

    def test_og_type_website(self):
        self.assertContains(
            self.resp,
            '<meta property="og:type" content="website">',
        )

    def test_og_site_name(self):
        self.assertContains(
            self.resp,
            '<meta property="og:site_name" content="Test Blog">',
        )

    def test_twitter_card_summary(self):
        self.assertContains(
            self.resp,
            '<meta name="twitter:card" content="summary">',
        )

    def test_rss_feed_discovery_link(self):
        self.assertContains(
            self.resp,
            '<link rel="alternate" type="application/rss+xml"',
        )


class RobotsTxtTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Namespace.objects.create(name="public")

    def test_robots_txt_status_and_content_type(self):
        resp = self.client.get("/robots.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/plain")

    def test_robots_txt_contains_sitemap(self):
        resp = self.client.get("/robots.txt")
        self.assertContains(resp, "Sitemap:")
        self.assertContains(resp, "/sitemap.xml")


class SitemapTest(SEOTestBase):
    def test_sitemap_returns_200(self):
        resp = self.client.get("/sitemap.xml")
        self.assertEqual(resp.status_code, 200)

    def test_sitemap_contains_published_post(self):
        resp = self.client.get("/sitemap.xml")
        self.assertContains(resp, self.post.get_absolute_url())

    def test_sitemap_excludes_unpublished(self):
        resp = self.client.get("/sitemap.xml")
        self.assertNotContains(resp, "/draft-post")

    def test_sitemap_excludes_private_namespace(self):
        resp = self.client.get("/sitemap.xml")
        self.assertNotContains(resp, "/private-post")
