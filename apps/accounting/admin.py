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


from .models import JournalEntry, JournalLine


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = (
        "organization",
        "base_debit_amount",
        "base_credit_amount",
        "created_at",
        "updated_at",
    )


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = (
        "entry_date",
        "reference",
        "description",
        "currency",
        "base_currency",
        "status",
    )
    list_filter = ("status", "currency", "entry_date")
    search_fields = ("reference", "description")
    readonly_fields = (
        "organization",
        "base_currency",
        "exchange_rate",
        "posted_at",
        "posted_by",
        "created_at",
        "updated_at",
    )
    inlines = (JournalLineInline,)


@admin.register(JournalLine)
class JournalLineAdmin(admin.ModelAdmin):
    list_display = (
        "entry",
        "account",
        "debit_amount",
        "credit_amount",
        "base_debit_amount",
        "base_credit_amount",
    )
    list_filter = ("account__category",)
    readonly_fields = (
        "organization",
        "base_debit_amount",
        "base_credit_amount",
        "created_at",
        "updated_at",
    )
