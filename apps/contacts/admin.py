from django.contrib import admin

from apps.access.services import has_permission
from apps.core.tenancy import get_current_org_id

from .models import Contact, ContactAddress, ContactRole


class ContactAdminPermissionMixin:
    """Use Leznard contact permissions rather than Django's unrelated model perms."""

    def _allowed(self, request, code):
        if request.user.is_superuser:
            return True
        organization_id = get_current_org_id()
        return bool(
            organization_id
            and has_permission(
                request.user,
                code,
                organization_id=organization_id,
            )
        )

    def has_module_permission(self, request):
        return self._allowed(request, "contacts.contact.view") or self._allowed(
            request, "contacts.contact.manage"
        )

    def has_view_permission(self, request, obj=None):
        return self._allowed(request, "contacts.contact.view")

    def has_add_permission(self, request):
        return self._allowed(request, "contacts.contact.manage")

    def has_change_permission(self, request, obj=None):
        return self._allowed(request, "contacts.contact.manage")

    def has_delete_permission(self, request, obj=None):
        return self._allowed(request, "contacts.contact.manage")


class ContactRoleInline(ContactAdminPermissionMixin, admin.TabularInline):
    model = ContactRole
    extra = 0
    fields = ("role",)


class ContactAddressInline(ContactAdminPermissionMixin, admin.TabularInline):
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
class ContactAdmin(ContactAdminPermissionMixin, admin.ModelAdmin):
    list_display = (
        "display_name",
        "contact_type",
        "email",
        "phone",
        "is_active",
        "updated_at",
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
    actions = ("deactivate_contacts", "activate_contacts")

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

    # Contacts are deactivated, not hard-deleted, to preserve business history.
    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Deactivate selected contacts")
    def deactivate_contacts(self, request, queryset):
        for contact in queryset:
            if contact.is_active:
                contact.is_active = False
                contact.save(update_fields=("is_active", "updated_at"))

    @admin.action(description="Reactivate selected contacts")
    def activate_contacts(self, request, queryset):
        for contact in queryset:
            if not contact.is_active:
                contact.is_active = True
                contact.save(update_fields=("is_active", "updated_at"))


@admin.register(ContactRole)
class ContactRoleAdmin(ContactAdminPermissionMixin, admin.ModelAdmin):
    list_display = ("contact", "role", "organization")
    list_filter = ("role",)
    search_fields = ("contact__display_name", "contact__email")
    readonly_fields = ("organization", "id", "created_at", "updated_at")


@admin.register(ContactAddress)
class ContactAddressAdmin(ContactAdminPermissionMixin, admin.ModelAdmin):
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
