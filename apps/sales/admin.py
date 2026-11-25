from django.contrib import admin

from .models import PurchaseBill, PurchaseBillLine, SalesInvoice, SalesInvoiceLine


class TenantReadOnlyAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        memberships = getattr(request.user, "memberships", None)
        if memberships is None:
            return self.model.unscoped.none()
        organization_ids = list(
            memberships.filter(status="active").values_list("organization_id", flat=True)
        )
        selected = request.session.get("active_organization_id")
        if selected:
            if str(selected) not in {str(value) for value in organization_ids}:
                return self.model.unscoped.none()
            return self.model.unscoped.filter(organization_id=selected)
        if len(organization_ids) == 1:
            return self.model.unscoped.filter(organization_id=organization_ids[0])
        return self.model.unscoped.none()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SalesInvoiceLineInline(admin.TabularInline):
    model = SalesInvoiceLine
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in SalesInvoiceLine._meta.fields]


class PurchaseBillLineInline(admin.TabularInline):
    model = PurchaseBillLine
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in PurchaseBillLine._meta.fields]


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(TenantReadOnlyAdmin):
    list_display = ("number", "customer", "document_date", "due_date", "status", "currency")
    list_filter = ("status", "currency")
    search_fields = ("number", "customer__display_name")
    readonly_fields = [field.name for field in SalesInvoice._meta.fields]
    inlines = (SalesInvoiceLineInline,)


@admin.register(PurchaseBill)
class PurchaseBillAdmin(TenantReadOnlyAdmin):
    list_display = ("number", "supplier", "supplier_reference", "document_date", "due_date", "status")
    list_filter = ("status", "currency")
    search_fields = ("number", "supplier__display_name", "supplier_reference")
    readonly_fields = [field.name for field in PurchaseBill._meta.fields]
    inlines = (PurchaseBillLineInline,)
