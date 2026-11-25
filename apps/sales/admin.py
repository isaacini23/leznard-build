from django.contrib import admin

from apps.core.tenancy import organization_context

from .models import PurchaseBill, PurchaseBillLine, SalesInvoice, SalesInvoiceLine


class TenantAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        org_id = request.session.get("active_organization_id")
        if org_id:
            return queryset.filter(organization_id=org_id)
        memberships = getattr(request.user, "memberships", None)
        if memberships is not None:
            ids = list(memberships.filter(status="active").values_list("organization_id", flat=True))
            return queryset.filter(organization_id__in=ids) if len(ids) == 1 else queryset.none()
        return queryset.none()


class SalesInvoiceLineInline(admin.TabularInline):
    model = SalesInvoiceLine
    extra = 0
    can_delete = False


class PurchaseBillLineInline(admin.TabularInline):
    model = PurchaseBillLine
    extra = 0
    can_delete = False


@admin.register(SalesInvoice)
class SalesInvoiceAdmin(TenantAdmin):
    list_display = ("number", "customer", "document_date", "due_date", "status", "currency")
    list_filter = ("status", "currency")
    search_fields = ("number", "customer__display_name")
    readonly_fields = ("organization", "status", "journal_entry", "posted_at", "posted_by", "created_at", "updated_at")
    inlines = (SalesInvoiceLineInline,)


@admin.register(PurchaseBill)
class PurchaseBillAdmin(TenantAdmin):
    list_display = ("number", "supplier", "supplier_reference", "document_date", "due_date", "status")
    list_filter = ("status", "currency")
    search_fields = ("number", "supplier__display_name", "supplier_reference")
    readonly_fields = ("organization", "status", "journal_entry", "posted_at", "posted_by", "created_at", "updated_at")
    inlines = (PurchaseBillLineInline,)
