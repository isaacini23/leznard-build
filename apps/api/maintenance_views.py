"""Maintenance requests: repair tickets per property or unit."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.contacts.models import Contact
from apps.properties import services
from apps.properties.models import MaintenanceRequest, Property, Unit


class MaintenanceSerializer(serializers.ModelSerializer):
    property = serializers.PrimaryKeyRelatedField(queryset=Property.objects.none())
    unit = serializers.PrimaryKeyRelatedField(queryset=Unit.objects.none(), allow_null=True, required=False)
    reported_by = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.none(), allow_null=True, required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.none(), allow_null=True, required=False)
    property_name = serializers.CharField(source="property.name", read_only=True)
    unit_label = serializers.CharField(source="unit.label", read_only=True, default=None)
    assigned_to_name = serializers.CharField(source="assigned_to.display_name", read_only=True, default=None)

    class Meta:
        model = MaintenanceRequest
        fields = [
            "id", "property", "property_name", "unit", "unit_label", "reported_by",
            "assigned_to", "assigned_to_name", "title", "description", "category", "priority",
            "status", "reported_date", "scheduled_date", "completed_date",
            "estimated_cost", "actual_cost", "resolution_notes", "purchase_bill",
        ]
        read_only_fields = ["id", "status", "completed_date", "actual_cost", "resolution_notes", "purchase_bill"]
        extra_kwargs = {"reported_date": {"required": False}}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = Property.objects.all()
        self.fields["unit"].queryset = Unit.objects.all()
        self.fields["reported_by"].queryset = Contact.objects.all()
        self.fields["assigned_to"].queryset = Contact.objects.all()


def _error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    return Response({"error": {"details": detail}}, status=400)


class _Base(APIView):
    def get_permissions(self):
        code = "properties.maintenance.view" if self.request.method == "GET" else "properties.maintenance.manage"
        return [IsAuthenticated(), require(code)()]


class MaintenanceListCreateView(_Base):
    def get(self, request):
        qs = MaintenanceRequest.objects.select_related("property", "unit", "assigned_to")
        params = request.query_params
        for key, field in (("property", "property_id"), ("unit", "unit_id"), ("status", "status"),
                           ("priority", "priority"), ("category", "category"), ("assigned_to", "assigned_to_id")):
            if params.get(key):
                qs = qs.filter(**{field: params[key]})
        if params.get("active") == "1":
            qs = qs.filter(status__in=("open", "in_progress"))
        return Response(MaintenanceSerializer(qs, many=True).data)

    def post(self, request):
        serializer = MaintenanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                obj = serializer.save(
                    reported_date=serializer.validated_data.get("reported_date") or timezone.localdate()
                )
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(MaintenanceSerializer(obj).data, status=status.HTTP_201_CREATED)


class MaintenanceDetailView(_Base):
    def get(self, request, pk):
        obj = MaintenanceRequest.objects.filter(pk=pk).first()
        if obj is None:
            return Response(status=404)
        return Response(MaintenanceSerializer(obj).data)

    def patch(self, request, pk):
        obj = MaintenanceRequest.objects.filter(pk=pk).first()
        if obj is None:
            return Response(status=404)
        if obj.status in (MaintenanceRequest.Status.COMPLETED, MaintenanceRequest.Status.CANCELLED):
            return Response({"error": {"details": ["A finished request cannot be edited."]}}, status=400)
        serializer = MaintenanceSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                obj = serializer.save()
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(MaintenanceSerializer(obj).data)


def _date(value, field, required=False):
    if not value:
        if required:
            raise DjangoValidationError({field: "This date is required."})
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise DjangoValidationError({field: "Use a date like 2026-12-31."})


class _Action(_Base):
    def get_permissions(self):
        return [IsAuthenticated(), require("properties.maintenance.manage")()]

    def run(self, request, pk):
        raise NotImplementedError

    def post(self, request, pk):
        if not MaintenanceRequest.objects.filter(pk=pk).exists():
            return Response(status=404)
        try:
            obj = self.run(request, pk)
        except DjangoValidationError as exc:
            return _error(exc)
        return Response(MaintenanceSerializer(obj).data)


class MaintenanceStartView(_Action):
    def run(self, request, pk):
        return services.start_maintenance(pk, scheduled_date=_date(request.data.get("scheduled_date"), "scheduled_date"))


class MaintenanceCompleteView(_Action):
    def run(self, request, pk):
        cost = request.data.get("actual_cost")
        try:
            cost = None if cost in (None, "") else Decimal(str(cost))
        except (InvalidOperation, TypeError, ValueError):
            raise DjangoValidationError({"actual_cost": "Enter a valid amount."})
        if cost is not None and not cost.is_finite():
            raise DjangoValidationError({"actual_cost": "Enter a finite amount."})
        if cost is not None and cost < 0:
            raise DjangoValidationError({"actual_cost": "Cost cannot be negative."})
        return services.complete_maintenance(
            pk,
            completed_date=_date(request.data.get("completed_date"), "completed_date") or timezone.localdate(),
            actual_cost=cost,
            resolution_notes=str(request.data.get("resolution_notes") or ""),
        )


class MaintenanceCancelView(_Action):
    def run(self, request, pk):
        return services.cancel_maintenance(pk, reason=str(request.data.get("reason") or ""))


class MaintenanceBillView(_Action):
    """Bill a completed job: creates the contractor's purchase bill (optionally posted)."""

    def run(self, request, pk):
        from apps.access.services import has_permission
        from apps.core.tenancy import get_current_org_id

        post = request.data.get("post") in (True, "true", "1", 1)
        if post and not has_permission(request.user, "purchases.bill.post", organization_id=get_current_org_id()):
            raise DjangoValidationError({"post": "You do not have permission to post bills; leave it as a draft."})
        return services.bill_maintenance(
            pk, bill_date=_date(request.data.get("bill_date"), "bill_date"), post=post, posted_by=request.user,
        )
