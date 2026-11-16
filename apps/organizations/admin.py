from django.contrib import admin

from .models import Membership, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "base_currency", "created_at")
    search_fields = ("name", "legal_name")


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "status", "created_at")
    list_filter = ("status",)