"""
Django settings for running tests.
"""

from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = True

SECRET_KEY = "test-secret-key-for-django-magic-authorization"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.sitemaps",
    "markdownify.apps.MarkdownifyConfig",
    "reversion",
    "martor",
    "django_magic_authorization",
    "django_mosaic",
    "django_mosaic.atproto",
]

MOSAIC_ATPROTO = {
    "HANDLE": "blog.example.com",
    "APP_PASSWORD": "test-app-password",
    "DID": "did:plc:testuser123",
    "PDS_URL": "https://pds.example.com",
    "PUBLICATION": {
        "NAME": "Test Blog",
        "URL": "https://blog.example.com",
        "DESCRIPTION": "A test blog.",
    },
    # Individual tests opt in via override_settings + captureOnCommitCallbacks
    # so the suite never fires network syncs implicitly.
    "AUTO_PUBLISH": False,
}

# Martor markdown editor (admin)
MARTOR_THEME = "bootstrap"
MARTOR_UPLOAD_URL = "/admin/django_mosaic/post/upload-image/"
MARTOR_MARKDOWN_EXTENSIONS = [
    "markdown.extensions.extra",
    "markdown.extensions.codehilite",
]
MARTOR_ENABLE_CONFIGS = {
    "emoji": "false",
    "imgur": "true",  # enables the image-upload toolbar button
    "mention": "false",
    "jquery": "true",
    "living": "false",
    "spellcheck": "false",
    "hljs": "true",
}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

USE_TZ = True

# Keep test uploads out of the repo working tree.
MEDIA_ROOT = BASE_DIR / ".test-media"
MEDIA_URL = "/media/"

CONSTANTS = {
    "site": {
        "title": "Test Blog",
        "description": "A test blog.",
    }
}

# Required for URL resolution
ROOT_URLCONF = "tests.urls"

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_magic_authorization.middleware.MagicAuthorizationMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "tests" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django_mosaic.context_processors.author",
            ],
        },
    }
]
