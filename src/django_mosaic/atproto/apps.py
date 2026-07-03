from django.apps import AppConfig


class AtprotoConfig(AppConfig):
    name = "django_mosaic.atproto"
    label = "django_mosaic_atproto"
    verbose_name = "Mosaic ATProto bridge"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import signals  # noqa: F401  (connects post_save handlers)
