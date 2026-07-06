from django.core.validators import RegexValidator
from django.db import models

# DNS label rules, minus leading/trailing hyphens; length capped at 63.
subdomain_validator = RegexValidator(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$",
    "Use 1-63 lowercase letters, digits, or hyphens (no leading/trailing hyphen).",
)


class Tenant(models.Model):
    """One hosted personal site: an ATProto account bound to a subdomain.

    The registry is deliberately thin — all content and (eventually) site
    configuration live in the tenant's own PDS repo, so this row plus their
    handle reproduces the whole site.
    """

    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_SUSPENDED, "Suspended"),
    ]

    did = models.CharField(max_length=256, unique=True)
    handle = models.CharField(max_length=256)
    subdomain = models.SlugField(
        max_length=63, unique=True, validators=[subdomain_validator]
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.subdomain} ({self.handle})"
