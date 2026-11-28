"""Late fees, reminders, owner payouts and the tenant portal."""
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.contacts.models import Contact
from apps.core.tenancy import organization_context
from apps.properties import services, tenant_space
from apps.properties.models import (
    MaintenanceRequest,
    OwnerPayout,
    Property,
    Tenancy,
    TenantPortalAccess,
)

from .property_views import DepositTransactionSerializer, _deposit_payload


def _error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    return Response({"error": {"details": detail}}, status=400)


def _truthy(value):
    return value in (True, "true", "1", 1)


def _date(value, field, required=True):
    if not value:
        if required:
            raise ValidationError({field: "This date is required."})
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date like 2027-01-31."})


# ---- late fees and reminders --------------------------------------------------------


class ApplyLateFeesView(APIView):
    """POST /rent-schedules/apply-late-fees/ {post?}: charge every overdue rent its late fee, once."""

    def get_permissions(self):
        return [IsAuthenticated(), require("properties.lease.manage")()]

    def post(self, request):
        try:
            result = tenant_space.apply_late_fees(
                timezone.localdate(), post=_truthy(request.data.get("post")), posted_by=request.user
            )
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(result)


class RentRemindersView(APIView):
    """GET /reports/rent-reminders/?days_before=3: message text for each overdue or soon-due rent."""

    def get_permissions(self):
        return [IsAuthenticated(), require("properties.lease.view")()]

    def get(self, request):
        try:
            days = int(request.query_params.get("days_before", 3))
            if not 0 <= days <= 60:
                raise ValueError
        except ValueError:
            raise ValidationError({"days_before": "Use a whole number from 0 to 60."})
        return Response(tenant_space.rent_reminders(timezone.localdate(), days_before=days))


# ---- owner payouts -----------------------------------------------------------------------


class OwnerPayoutSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.display_name", read_only=True)

    class Meta:
        model = OwnerPayout
        fields = ["id", "property", "owner", "owner_name", "period_start", "period_end",
                  "amount", "payout_date", "reference"]


class OwnerPayoutsView(APIView):
    def get_permissions(self):
        code = "properties.profitability.view" if self.request.method == "GET" else "properties.payment.record"
        return [IsAuthenticated(), require(code)()]

    def get(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return Response(status=404)
        return Response(OwnerPayoutSerializer(OwnerPayout.objects.filter(property=prop), many=True).data)

    def post(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return Response(status=404)
        data = request.data
        owner = Contact.objects.filter(pk=data.get("owner")).first() if data.get("owner") else None
        if owner is None:
            raise ValidationError({"owner": "Choose the owner to pay."})
        try:
            payout = tenant_space.record_owner_payout(
                prop, owner_id=owner.pk,
                period_start=_date(data.get("period_start"), "period_start"),
                period_end=_date(data.get("period_end"), "period_end"),
                amount=data.get("amount"), payout_date=_date(data.get("payout_date"), "payout_date", False),
                account_id=data.get("account") or None, reference=str(data.get("reference") or ""),
                posted_by=request.user,
            )
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(OwnerPayoutSerializer(payout).data, status=status.HTTP_201_CREATED)


# ---- tenant portal: invitations (landlord side) ---------------------------------------


class PortalInviteView(APIView):
    """POST /contacts/{id}/portal-invite/: a one-time code the tenant uses to open their portal."""

    def get_permissions(self):
        return [IsAuthenticated(), require("properties.lease.manage")()]

    def post(self, request, contact_id):
        contact = Contact.objects.filter(pk=contact_id).first()
        if contact is None:
            return Response(status=404)
        try:
            invitation, token = tenant_space.create_portal_invitation(contact, created_by=request.user)
        except DjangoValidationError as exc:
            return _error(exc)
        return Response({"token": token, "expires_at": invitation.expires_at}, status=status.HTTP_201_CREATED)


# ---- tenant portal: the tenant's own view ---------------------------------------------------


class _PortalView(APIView):
    """Signed-in tenants only see their own records, in the company they were invited by."""

    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        accesses = list(
            TenantPortalAccess.objects.filter(user=request.user, is_active=True).select_related("organization")
        )
        wanted = request.query_params.get("company")
        if wanted:
            accesses = [a for a in accesses if str(a.organization_id) == wanted]
        if not accesses:
            raise serializers.ValidationError({"portal": "You do not have tenant portal access."})
        if len(accesses) > 1:
            raise serializers.ValidationError({"company": "Choose a company with ?company=<id>."})
        self.access = accesses[0]

    def contact(self):
        """The tenant's contact record. Call inside organization_context (contacts are company data)."""
        return Contact.objects.get(pk=self.access.contact_id)


class PortalAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            access = tenant_space.accept_portal_invitation(request.user, request.data.get("token"))
        except DjangoValidationError as exc:
            return _error(exc)
        return Response({"company": str(access.organization_id), "tenant": str(access.contact_id)},
                        status=status.HTTP_201_CREATED)


class PortalHomeView(_PortalView):
    def get(self, request):
        with organization_context(self.access.organization_id):
            tenancies = Tenancy.objects.filter(tenant_id=self.access.contact_id).select_related("unit__property")
            return Response({
                "company": self.access.organization.name,
                "tenant": self.contact().display_name,
                "tenancies": [{
                    "id": str(t.pk), "property": t.unit.property.name, "unit": t.unit.label,
                    "status": t.status, "start_date": t.start_date, "end_date": t.end_date,
                    "rent_amount": str(t.rent_amount), "rent_frequency": t.rent_frequency,
                } for t in tenancies],
            })


class PortalRentView(_PortalView):
    def get(self, request):
        with organization_context(self.access.organization_id):
            return Response(tenant_space.portal_rent(self.contact()))


class PortalDepositView(_PortalView):
    def get(self, request):
        with organization_context(self.access.organization_id):
            tenancy = (
                Tenancy.objects.filter(tenant_id=self.access.contact_id)
                .exclude(status=Tenancy.Status.DRAFT).order_by("-start_date").first()
            )
            if tenancy is None:
                return Response({"held": "0.00", "transactions": []})
            payload = _deposit_payload(tenancy)
            return Response({k: payload[k] for k in ("agreed", "received", "deducted", "refunded", "held", "transactions")})


class PortalMaintenanceSerializer(serializers.ModelSerializer):
    unit_label = serializers.CharField(source="unit.label", read_only=True, default=None)

    class Meta:
        model = MaintenanceRequest
        fields = ["id", "unit", "unit_label", "title", "description", "category", "priority",
                  "status", "reported_date", "completed_date", "resolution_notes"]


class PortalMaintenanceView(_PortalView):
    def get(self, request):
        with organization_context(self.access.organization_id):
            qs = MaintenanceRequest.objects.filter(reported_by_id=self.access.contact_id).select_related("unit")
            return Response(PortalMaintenanceSerializer(qs, many=True).data)

    def post(self, request):
        data = request.data
        with organization_context(self.access.organization_id):
            try:
                obj = tenant_space.portal_report_repair(
                    self.contact(), unit_id=data.get("unit"), title=data.get("title", ""),
                    description=str(data.get("description") or ""),
                    category=data.get("category") if data.get("category") in MaintenanceRequest.Category.values else "other",
                    priority=data.get("priority") if data.get("priority") in MaintenanceRequest.Priority.values else "normal",
                )
            except DjangoValidationError as exc:
                return _error(exc)
            return Response(PortalMaintenanceSerializer(obj).data, status=status.HTTP_201_CREATED)
