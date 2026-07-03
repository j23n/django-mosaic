from django.apps import AppConfig
from django.conf import settings
from django.core.checks import Warning, register


class BlogConfig(AppConfig):
    name = "django_mosaic"
    verbose_name = "Mosaic"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        register(check_magic_authorization_middleware)


def check_magic_authorization_middleware(app_configs, **kwargs):
    """Warn if the private namespace is served without its enforcing middleware.

    ``protected_path`` only tags URL patterns; enforcement lives entirely in
    ``MagicAuthorizationMiddleware``. Without it, ``/private/`` is world-readable.
    """
    middleware = "django_magic_authorization.middleware.MagicAuthorizationMiddleware"
    if middleware not in set(getattr(settings, "MIDDLEWARE", [])):
        return [
            Warning(
                "MagicAuthorizationMiddleware is not installed; the private "
                "namespace will be served without access control.",
                hint=f"Add '{middleware}' to MIDDLEWARE.",
                id="django_mosaic.W001",
            )
        ]
    return []
