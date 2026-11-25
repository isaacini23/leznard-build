from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.core.tenancy import get_current_org_id

from .models import PurchaseBill, SalesInvoice
from .serializers import PurchaseBillSerializer, SalesInvoiceSerializer
from .services import post_purchase_bill, post_sales_invoice


def _translate_error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    raise ValidationError(detail)


class SalesInvoiceListCreateView(APIView):
    permission_classes = [require("sales.invoice.view")]

    def get(self, request):
        queryset = SalesInvoice.objects.select_related("customer").prefetch_related("lines")
        state = request.query_params.get("status")
        if state:
            queryset = queryset.filter(status=state)
        return Response(SalesInvoiceSerializer(queryset.order_by("-document_date", "-created_at"), many=True, context={"organization_id": get_current_org_id()}).data)

    def post(self, request):
        if not request.user.has_perm("sales.invoice.manage"):
            from apps.access.services import has_permission
            if not has_permission(request.user, "sales.invoice.manage", organization_id=get_current_org_id()):
                return Response({"detail": "You do not have permission to manage sales invoices."}, status=403)
        serializer = SalesInvoiceSerializer(data=request.data, context={"organization_id": get_current_org_id()})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                invoice = serializer.save()
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(SalesInvoiceSerializer(invoice, context={"organization_id": get_current_org_id()}).data, status=status.HTTP_201_CREATED)


class SalesInvoiceDetailView(APIView):
    permission_classes = [require("sales.invoice.view")]

    def get_object(self, invoice_id):
        from django.shortcuts import get_object_or_404
        return get_object_or_404(SalesInvoice.objects.prefetch_related("lines"), pk=invoice_id)

    def get(self, request, invoice_id):
        return Response(SalesInvoiceSerializer(self.get_object(invoice_id), context={"organization_id": get_current_org_id()}).data)

    def patch(self, request, invoice_id):
        from apps.access.services import has_permission
        if not has_permission(request.user, "sales.invoice.manage", organization_id=get_current_org_id()):
            return Response({"detail": "You do not have permission to manage sales invoices."}, status=403)
        invoice = self.get_object(invoice_id)
        serializer = SalesInvoiceSerializer(invoice, data=request.data, partial=True, context={"organization_id": get_current_org_id()})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                invoice = serializer.save()
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(SalesInvoiceSerializer(invoice, context={"organization_id": get_current_org_id()}).data)

    def post(self, request, invoice_id):
        from apps.access.services import has_permission
        if not has_permission(request.user, "sales.invoice.post", organization_id=get_current_org_id()):
            return Response({"detail": "You do not have permission to post sales invoices."}, status=403)
        invoice = self.get_object(invoice_id)
        try:
            invoice = post_sales_invoice(invoice, posted_by=request.user)
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(SalesInvoiceSerializer(invoice, context={"organization_id": get_current_org_id()}).data)


class PurchaseBillListCreateView(APIView):
    permission_classes = [require("purchases.bill.view")]

    def get(self, request):
        queryset = PurchaseBill.objects.select_related("supplier").prefetch_related("lines")
        state = request.query_params.get("status")
        if state:
            queryset = queryset.filter(status=state)
        return Response(PurchaseBillSerializer(queryset.order_by("-document_date", "-created_at"), many=True, context={"organization_id": get_current_org_id()}).data)

    def post(self, request):
        from apps.access.services import has_permission
        if not has_permission(request.user, "purchases.bill.manage", organization_id=get_current_org_id()):
            return Response({"detail": "You do not have permission to manage purchase bills."}, status=403)
        serializer = PurchaseBillSerializer(data=request.data, context={"organization_id": get_current_org_id()})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                bill = serializer.save()
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(PurchaseBillSerializer(bill, context={"organization_id": get_current_org_id()}).data, status=status.HTTP_201_CREATED)


class PurchaseBillDetailView(APIView):
    permission_classes = [require("purchases.bill.view")]

    def get_object(self, bill_id):
        from django.shortcuts import get_object_or_404
        return get_object_or_404(PurchaseBill.objects.prefetch_related("lines"), pk=bill_id)

    def get(self, request, bill_id):
        return Response(PurchaseBillSerializer(self.get_object(bill_id), context={"organization_id": get_current_org_id()}).data)

    def patch(self, request, bill_id):
        from apps.access.services import has_permission
        if not has_permission(request.user, "purchases.bill.manage", organization_id=get_current_org_id()):
            return Response({"detail": "You do not have permission to manage purchase bills."}, status=403)
        bill = self.get_object(bill_id)
        serializer = PurchaseBillSerializer(bill, data=request.data, partial=True, context={"organization_id": get_current_org_id()})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                bill = serializer.save()
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(PurchaseBillSerializer(bill, context={"organization_id": get_current_org_id()}).data)

    def post(self, request, bill_id):
        from apps.access.services import has_permission
        if not has_permission(request.user, "purchases.bill.post", organization_id=get_current_org_id()):
            return Response({"detail": "You do not have permission to post purchase bills."}, status=403)
        bill = self.get_object(bill_id)
        try:
            bill = post_purchase_bill(bill, posted_by=request.user)
        except (DjangoValidationError, IntegrityError) as exc:
            _translate_error(exc)
        return Response(PurchaseBillSerializer(bill, context={"organization_id": get_current_org_id()}).data)
