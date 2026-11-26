from django.contrib import admin

from .models import Payment, PaymentAllocation


class TenantReadOnlyPaymentAdmin(admin.ModelAdmin):
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

    def has_view_permission(self, request, obj=None):
        from apps.access.services import has_permission
        organization_id = request.session.get("active_organization_id")
        if not organization_id:
            memberships = getattr(request.user, "memberships", None)
            if memberships is None:
                return False
            ids = list(memberships.filter(status="active").values_list("organization_id", flat=True))
            organization_id = ids[0] if len(ids) == 1 else None
        return bool(organization_id and has_permission(
            request.user, "payments.payment.view", organization_id=organization_id
        ))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    can_delete = False
    readonly_fields = [field.name for field in PaymentAllocation._meta.fields]


@admin.register(Payment)
class PaymentAdmin(TenantReadOnlyPaymentAdmin):
    list_display = ("payment_date", "payment_type", "contact", "amount", "currency", "status", "method")
    list_filter = ("payment_type", "status", "currency", "method")
    search_fields = ("reference", "contact__display_name")
    readonly_fields = [field.name for field in Payment._meta.fields]
    inlines = (PaymentAllocationInline,)
