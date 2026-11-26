from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.services import has_permission
from apps.core.tenancy import get_current_org_id
from apps.contacts.models import ContactRole
from apps.sales.models import SalesInvoice

from .models import Payment, PaymentAllocation
from .serializers import PaymentSerializer
from .services import post_payment


def _translate_error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    raise ValidationError(detail)


class _PaymentListPermission(BasePermission):
    def has_permission(self, request, view):
        org_id = get_current_org_id()
        if request.method == "GET":
            return (
                has_permission(request.user, "payments.payment.view", organization_id=org_id)
                or has_permission(request.user, "properties.payment.record", organization_id=org_id)
            )
        if request.method == "POST":
            return (
                has_permission(request.user, "payments.payment.manage", organization_id=org_id)
                or has_permission(request.user, "properties.payment.record", organization_id=org_id)
            )
        return False


class _PaymentDetailPermission(BasePermission):
    def has_permission(self, request, view):
        org_id = get_current_org_id()
        if request.method == "GET":
            return (
                has_permission(request.user, "payments.payment.view", organization_id=org_id)
                or has_permission(request.user, "properties.payment.record", organization_id=org_id)
            )
        if request.method == "POST":
            return has_permission(request.user, "payments.payment.post", organization_id=org_id)
        return False


class PaymentListCreateView(APIView):
    permission_classes = [IsAuthenticated, _PaymentListPermission]

    def get(self, request):
        queryset = Payment.objects.select_related("contact", "account").prefetch_related(
            "allocations__sales_invoice", "allocations__purchase_bill"
        )
        payment_type = request.query_params.get("type")
        state = request.query_params.get("status")
        if not has_permission(
            request.user, "payments.payment.view", organization_id=get_current_org_id()
        ):
            queryset = queryset.filter(
                payment_type=Payment.Type.CUSTOMER_RECEIPT,
                contact__roles__role=ContactRole.Role.TENANT,
            ).distinct()
        if payment_type:
            queryset = queryset.filter(payment_type=payment_type)
        if state:
            queryset = queryset.filter(status=state)
        data = PaymentSerializer(
            queryset.order_by("-payment_date", "-created_at"), many=True,
            context={"organization_id": get_current_org_id()},
        ).data
        return Response(data)

    def post(self, request):
        can_manage_all = has_permission(
            request.user, "payments.payment.manage", organization_id=get_current_org_id()
        )
        can_record_rent = (
            has_permission(request.user, "properties.payment.record", organization_id=get_current_org_id())
            and request.data.get("payment_type") == Payment.Type.CUSTOMER_RECEIPT
        )
        if not (can_manage_all or can_record_rent):
            return Response({"detail": "You do not have permission to record this payment."}, status=403)
        if can_record_rent and not can_manage_all:
            org_id = get_current_org_id()
            if not ContactRole.unscoped.filter(
                organization_id=org_id,
                contact_id=request.data.get("contact"),
                role=ContactRole.Role.TENANT,
            ).exists():
                return Response({"detail": "Property payment recording is limited to tenant contacts."}, status=403)
            allocations = request.data.get("allocations") or []
            if not allocations:
                return Response({"detail": "Allocate a rent receipt to at least one posted rent invoice."}, status=400)
            for allocation in allocations:
                if not isinstance(allocation, dict):
                    return Response({"detail": "Each rent allocation must be an object."}, status=400)
                invoice_id = allocation.get("sales_invoice")
                if not invoice_id or allocation.get("purchase_bill"):
                    return Response({"detail": "Property officers can allocate receipts only to rent invoices."}, status=400)
                if not SalesInvoice.objects.filter(
                    pk=invoice_id, tenancy__isnull=False, status=SalesInvoice.Status.POSTED
                ).exists():
                    return Response({"detail": "Receipts can only be allocated to posted rent invoices."}, status=400)
        serializer = PaymentSerializer(
            data=request.data, context={"organization_id": get_current_org_id()}
        )
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                payment = serializer.save()
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(
            PaymentSerializer(payment, context={"organization_id": get_current_org_id()}).data,
            status=status.HTTP_201_CREATED,
        )


class PaymentDetailView(APIView):
    permission_classes = [IsAuthenticated, _PaymentDetailPermission]

    def get(self, request, payment_id):
        from django.shortcuts import get_object_or_404
        payment = get_object_or_404(
            Payment.objects.select_related("contact", "account").prefetch_related(
                "allocations__sales_invoice", "allocations__purchase_bill"
            ),
            pk=payment_id,
        )
        if not has_permission(request.user, "payments.payment.view", organization_id=get_current_org_id()):
            if (
                payment.payment_type != Payment.Type.CUSTOMER_RECEIPT
                or not ContactRole.unscoped.filter(
                    organization_id=get_current_org_id(),
                    contact_id=payment.contact_id,
                    role=ContactRole.Role.TENANT,
                ).exists()
            ):
                return Response(status=404)
        return Response(PaymentSerializer(
            payment, context={"organization_id": get_current_org_id()}
        ).data)

    def post(self, request, payment_id):
        if not has_permission(
            request.user, "payments.payment.post", organization_id=get_current_org_id()
        ):
            return Response({"detail": "You do not have permission to post payments."}, status=403)
        from django.shortcuts import get_object_or_404
        payment = get_object_or_404(Payment.objects.all(), pk=payment_id)
        try:
            payment = post_payment(payment, posted_by=request.user)
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(PaymentSerializer(
            payment, context={"organization_id": get_current_org_id()}
        ).data)
