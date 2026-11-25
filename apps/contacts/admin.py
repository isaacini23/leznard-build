from django.contrib import admin

from .models import Contact, ContactAddress, ContactRole


class ContactRoleInline(admin.TabularInline):
    model = ContactRole
    extra = 0
    fields = ("role",)


class ContactAddressInline(admin.TabularInline):
    model = ContactAddress
    extra = 0
    fields = (
        "address_type",
        "street_address",
        "address_line_2",
        "city",
        "state",
        "country",
        "postal_code",
        "is_default",
    )


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "contact_type",
        "email",
        "phone",
        "is_active",
    )
    list_filter = ("contact_type", "is_active")
    search_fields = (
        "display_name",
        "first_name",
        "last_name",
        "legal_name",
        "email",
        "phone",
        "tax_id",
    )
    ordering = ("display_name",)
    readonly_fields = ("organization", "id", "created_at", "updated_at")
    inlines = (ContactRoleInline, ContactAddressInline)

    fieldsets = (
        (
            "Contact information",
            {
                "fields": (
                    "contact_type",
                    "display_name",
                    "first_name",
                    "last_name",
                    "legal_name",
                )
            },
        ),
        ("Communication", {"fields": ("email", "phone", "website")}),
        ("Business information", {"fields": ("tax_id", "notes", "is_active")}),
        (
            "Record information",
            {
                "fields": (
                    "organization",
                    "id",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )


@admin.register(ContactRole)
class ContactRoleAdmin(admin.ModelAdmin):
    list_display = ("contact", "role", "organization")
    list_filter = ("role",)
    search_fields = ("contact__display_name", "contact__email")
    readonly_fields = ("organization", "id", "created_at", "updated_at")


@admin.register(ContactAddress)
class ContactAddressAdmin(admin.ModelAdmin):
    list_display = (
        "contact",
        "address_type",
        "city",
        "state",
        "country",
        "is_default",
    )
    list_filter = ("address_type", "country", "is_default")
    search_fields = ("contact__display_name", "city", "state")
    readonly_fields = ("organization", "id", "created_at", "updated_at")
