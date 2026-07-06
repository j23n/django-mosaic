from django.contrib import admin
from django.utils import timezone

from .models import Report, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = [
        "subdomain",
        "handle",
        "did",
        "custom_domain",
        "domain_verified_at",
        "status",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = ["subdomain", "handle", "did", "custom_domain"]
    readonly_fields = ["did", "domain_verified_at", "created_at", "updated_at"]
    actions = ["suspend", "reactivate"]

    @admin.action(description="Suspend selected tenants")
    def suspend(self, request, queryset):
        queryset.update(status=Tenant.STATUS_SUSPENDED)

    @admin.action(description="Reactivate selected tenants")
    def reactivate(self, request, queryset):
        queryset.update(status=Tenant.STATUS_ACTIVE)


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["tenant", "created_at", "resolved_at", "reason_excerpt"]
    list_filter = [("resolved_at", admin.EmptyFieldListFilter)]
    search_fields = ["tenant__subdomain", "tenant__handle", "reason"]
    readonly_fields = ["tenant", "reason", "reporter_contact", "created_at"]
    actions = ["resolve", "suspend_tenant"]

    @admin.display(description="Reason")
    def reason_excerpt(self, obj):
        return obj.reason[:80]

    @admin.action(description="Mark selected reports resolved")
    def resolve(self, request, queryset):
        queryset.update(resolved_at=timezone.now())

    @admin.action(description="Suspend the reported tenants")
    def suspend_tenant(self, request, queryset):
        Tenant.objects.filter(reports__in=queryset).update(
            status=Tenant.STATUS_SUSPENDED
        )
        queryset.update(resolved_at=timezone.now())
