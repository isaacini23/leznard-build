from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.access.services import has_permission
from apps.core.tenancy import get_current_org_id

from .models import Payment, PaymentAllocation
from .serializers import PaymentSerializer
from .services import post_payment


def _translate_error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    raise ValidationError(detail)


class PaymentListCreateView(APIView):
    permission_classes = [require("payments.payment.view")]

    def get(self, request):
        queryset = Payment.objects.select_related("contact", "account").prefetch_related(
            "allocations__sales_invoice", "allocations__purchase_bill"
        )
        payment_type = request.query_params.get("type")
        state = request.query_params.get("status")
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
        if not has_permission(
            request.user, "payments.payment.manage", organization_id=get_current_org_id()
        ):
            return Response({"detail": "You do not have permission to record payments."}, status=403)
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
    permission_classes = [require("payments.payment.view")]

    def get(self, request, payment_id):
        from django.shortcuts import get_object_or_404
        payment = get_object_or_404(
            Payment.objects.select_related("contact", "account").prefetch_related(
                "allocations__sales_invoice", "allocations__purchase_bill"
            ),
            pk=payment_id,
        )
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
