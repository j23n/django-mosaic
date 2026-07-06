from django.apps import AppConfig


class HostedConfig(AppConfig):
    name = "django_mosaic.hosted"
    label = "django_mosaic_hosted"
    verbose_name = "Mosaic hosted (multi-tenant)"
    default_auto_field = "django.db.models.BigAutoField"
