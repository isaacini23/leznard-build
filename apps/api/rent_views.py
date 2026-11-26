"""Rent billing API. Rent invoices remain ordinary sales invoices for accounting and receipts."""
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.accounting.models import Account
from apps.core.tenancy import get_current_org_id
from apps.payments.models import Payment, PaymentAllocation
from apps.properties import services
from apps.properties.models import RentCharge, RentSchedule, Tenancy


class RentScheduleSerializer(serializers.ModelSerializer):
    tenancy = serializers.PrimaryKeyRelatedField(read_only=True)
    revenue_account = serializers.PrimaryKeyRelatedField(queryset=Account.unscoped.none())

    class Meta:
        model = RentSchedule
        fields = (
            "id", "tenancy", "revenue_account", "next_invoice_date",
            "billing_day", "due_days", "is_active", "created_at", "updated_at",
        )
        read_only_fields = ("id", "tenancy", "billing_day", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["revenue_account"].queryset = Account.unscoped.filter(
                organization_id=org_id, category=Account.Category.INCOME, is_active=True
            )


class RentChargeSerializer(serializers.ModelSerializer):
    tenancy = serializers.PrimaryKeyRelatedField(read_only=True)
    invoice_id = serializers.UUIDField(read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    invoice_status = serializers.CharField(source="invoice.status", read_only=True)
    document_date = serializers.DateField(source="invoice.document_date", read_only=True)
    due_date = serializers.DateField(source="invoice.due_date", read_only=True)
    total = serializers.DecimalField(source="invoice.total", max_digits=20, decimal_places=4, read_only=True)
    amount_paid = serializers.SerializerMethodField()
    amount_due = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = RentCharge
        fields = (
            "id", "tenancy", "invoice_id", "invoice_number", "invoice_status",
            "period_start", "period_end", "document_date", "due_date", "total",
            "amount_paid", "amount_due", "status", "generated_at",
        )

    def _amount_paid(self, obj):
        return PaymentAllocation.unscoped.filter(
            organization_id=obj.organization_id,
            sales_invoice=obj.invoice,
            payment__status=Payment.Status.POSTED,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.0000")

    def get_amount_paid(self, obj):
        return format(self._amount_paid(obj), ".4f")

    def get_amount_due(self, obj):
        amount_due = max(obj.invoice.total - self._amount_paid(obj), Decimal("0.0000"))
        return format(amount_due, ".4f")

    def get_status(self, obj):
        if obj.invoice.status != obj.invoice.Status.POSTED:
            return "draft"
        paid = self._amount_paid(obj)
        due = max(obj.invoice.total - paid, Decimal("0.0000"))
        if due <= 0:
            return "paid"
        if obj.invoice.due_date < timezone.localdate():
            return "overdue"
        if paid > 0:
            return "partially_paid"
        return "unpaid"


class _RentAPIView(APIView):
    def get_permissions(self):
        code = "properties.lease.view" if self.request.method == "GET" else "properties.lease.manage"
        return [IsAuthenticated(), require(code)()]

    @staticmethod
    def model_error(exc):
        detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
        return Response({"error": {"details": detail}}, status=status.HTTP_400_BAD_REQUEST)


class TenancyRentScheduleView(_RentAPIView):
    def get(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.all(), pk=tenancy_id)
        schedule = RentSchedule.objects.filter(tenancy=tenancy).first()
        if schedule is None:
            return Response({"detail": "No rent schedule has been configured."}, status=404)
        return Response(RentScheduleSerializer(
            schedule, context={"organization_id": get_current_org_id()}
        ).data)

    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.all(), pk=tenancy_id)
        serializer = RentScheduleSerializer(
            data=request.data, context={"organization_id": get_current_org_id()}
        )
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                schedule = serializer.save(tenancy=tenancy)
        except DjangoValidationError as exc:
            return self.model_error(exc)
        except IntegrityError:
            return Response({"detail": "A rent schedule already exists for this tenancy."}, status=400)
        return Response(RentScheduleSerializer(
            schedule, context={"organization_id": get_current_org_id()}
        ).data, status=status.HTTP_201_CREATED)

    def patch(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.all(), pk=tenancy_id)
        schedule = get_object_or_404(RentSchedule.objects.all(), tenancy=tenancy)
        serializer = RentScheduleSerializer(
            schedule, data=request.data, partial=True,
            context={"organization_id": get_current_org_id()},
        )
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                schedule = serializer.save()
        except DjangoValidationError as exc:
            return self.model_error(exc)
        return Response(RentScheduleSerializer(
            schedule, context={"organization_id": get_current_org_id()}
        ).data)


class TenancyRentChargesView(_RentAPIView):
    def get(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.all(), pk=tenancy_id)
        charges = RentCharge.objects.filter(tenancy=tenancy).select_related(
            "invoice"
        ).order_by("-period_start")
        return Response(RentChargeSerializer(charges, many=True).data)


class RentScheduleGenerateView(_RentAPIView):
    def post(self, request, schedule_id):
        schedule = get_object_or_404(RentSchedule.objects.all(), pk=schedule_id)
        try:
            charge = services.generate_next_rent_invoice(schedule.pk)
        except DjangoValidationError as exc:
            return self.model_error(exc)
        except IntegrityError:
            return Response(
                {"detail": "This rent period has already been invoiced."}, status=400
            )
        return Response(RentChargeSerializer(charge).data, status=status.HTTP_201_CREATED)
