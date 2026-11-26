"""Properties and their units. People (owners, tenants) are contacts: see apps.contacts."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.contacts.models import Contact
from apps.properties.models import Property, Unit


def _duplicate_error():
    return Response(
        {"error": {"details": ["A record with these details already exists."]}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _save(serializer, **kwargs):
    """Save inside a savepoint so a unique-constraint clash becomes a clean 400."""
    with transaction.atomic():
        return serializer.save(**kwargs)


def _model_error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    return Response({"error": {"details": detail}}, status=status.HTTP_400_BAD_REQUEST)


# ---- serializers ----------------------------------------------------------------


class PropertySerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=Contact.objects.none(), allow_null=True, required=False
    )
    unit_count = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = [
            "id", "name", "property_type", "address_line", "city", "state", "country",
            "owner", "status", "description", "unit_count",
        ]
        read_only_fields = ["id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Evaluated per request, inside the active company, so only its contacts are valid.
        self.fields["owner"].queryset = Contact.objects.all()

    def get_unit_count(self, obj):
        return obj.units.count()


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = [
            "id", "property", "label", "unit_type", "rent_amount", "occupancy_status", "description",
        ]
        read_only_fields = ["id", "property"]


# ---- generic list/detail helpers ------------------------------------------------


class _ListCreate(APIView):
    serializer_class = None
    view_permission = None
    manage_permission = None

    def get_permissions(self):
        code = self.view_permission if self.request.method == "GET" else self.manage_permission
        return [IsAuthenticated(), require(code)()]

    def queryset(self, request):
        raise NotImplementedError

    def get(self, request, **kwargs):
        return Response(self.serializer_class(self.queryset(request, **kwargs), many=True).data)

    def perform_create(self, serializer, **kwargs):
        return _save(serializer)

    def post(self, request, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            instance = self.perform_create(serializer, **kwargs)
        except DjangoValidationError as exc:
            return _model_error(exc)
        except IntegrityError:
            return _duplicate_error()
        return Response(self.serializer_class(instance).data, status=status.HTTP_201_CREATED)


class _Detail(APIView):
    serializer_class = None
    view_permission = None
    manage_permission = None
    model = None

    def get_permissions(self):
        code = self.view_permission if self.request.method == "GET" else self.manage_permission
        return [IsAuthenticated(), require(code)()]

    def get_object(self, pk):
        return self.model.objects.filter(pk=pk).first()

    def get(self, request, pk):
        obj = self.get_object(pk)
        if obj is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(self.serializer_class(obj).data)

    def patch(self, request, pk):
        obj = self.get_object(pk)
        if obj is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = self.serializer_class(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            obj = _save(serializer)
        except DjangoValidationError as exc:
            return _model_error(exc)
        except IntegrityError:
            return _duplicate_error()
        return Response(self.serializer_class(obj).data)


# ---- properties and units -------------------------------------------------------


class PropertyListCreateView(_ListCreate):
    serializer_class = PropertySerializer
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"

    def queryset(self, request, **kwargs):
        return Property.objects.all()


class PropertyDetailView(_Detail):
    serializer_class = PropertySerializer
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"
    model = Property


class PropertyUnitsView(_ListCreate):
    serializer_class = UnitSerializer
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"

    def _property(self, property_id):
        return Property.objects.filter(pk=property_id).first()

    def get(self, request, property_id):
        if self._property(property_id) is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(UnitSerializer(Unit.objects.filter(property_id=property_id), many=True).data)

    def post(self, request, property_id):
        prop = self._property(property_id)
        if prop is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = UnitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            unit = _save(serializer, property=prop)
        except DjangoValidationError as exc:
            return _model_error(exc)
        except IntegrityError:
            return _duplicate_error()
        return Response(UnitSerializer(unit).data, status=status.HTTP_201_CREATED)


class UnitDetailView(_Detail):
    serializer_class = UnitSerializer
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"
    model = Unit
