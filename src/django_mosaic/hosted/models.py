from django.core.validators import RegexValidator
from django.db import models

# DNS label rules, minus leading/trailing hyphens; length capped at 63.
subdomain_validator = RegexValidator(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$",
    "Use 1-63 lowercase letters, digits, or hyphens (no leading/trailing hyphen).",
)

# A full hostname: dotted lowercase labels with an alphabetic TLD.
domain_validator = RegexValidator(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    "Enter a bare domain name like blog.example.com (lowercase, no scheme or path).",
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
    # Custom domain (optional). Verification is implicit: TLS certs are only
    # issued through the on-demand `ask` endpoint, and issuance succeeds only
    # if the domain's DNS actually points at us — the first request that
    # arrives with this Host stamps domain_verified_at.
    custom_domain = models.CharField(
        max_length=253,
        null=True,
        blank=True,
        unique=True,
        validators=[domain_validator],
    )
    domain_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.subdomain} ({self.handle})"


class Report(models.Model):
    """An abuse/content report filed against a tenant site."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="reports")
    reason = models.TextField(max_length=2000)
    reporter_contact = models.CharField(max_length=320, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"report on {self.tenant} ({self.created_at:%Y-%m-%d})"
