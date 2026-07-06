from django.contrib import admin

from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["subdomain", "handle", "did", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["subdomain", "handle", "did"]
    readonly_fields = ["did", "created_at", "updated_at"]
    actions = ["suspend", "reactivate"]

    @admin.action(description="Suspend selected tenants")
    def suspend(self, request, queryset):
        queryset.update(status=Tenant.STATUS_SUSPENDED)

    @admin.action(description="Reactivate selected tenants")
    def reactivate(self, request, queryset):
        queryset.update(status=Tenant.STATUS_ACTIVE)
