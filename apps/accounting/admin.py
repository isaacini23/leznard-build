from django.contrib import admin

from .models import Account


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "code", "name", "category", "account_type",
        "normal_balance", "is_system", "is_active",
    )
    list_filter = ("category", "account_type", "is_system", "is_active")
    search_fields = ("code", "name")
    ordering = ("code",)
    readonly_fields = ("organization", "is_system", "created_at", "updated_at")
