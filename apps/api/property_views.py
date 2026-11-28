"""Real-estate API: properties, buildings, units, ownership, management, documents,
tenant profiles and tenancies. People (owners, tenants, managers) are Contacts."""
import hashlib
from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.contacts.models import Contact
from apps.properties import services
from apps.properties.models import (
    Building,
    DepositTransaction,
    Property,
    PropertyDocument,
    PropertyManagement,
    PropertyOwnership,
    Tenancy,
    TenantProfile,
    Unit,
)

# ---- error helpers --------------------------------------------------------------


def _duplicate_error():
    return Response(
        {"error": {"details": ["A record with these details already exists."]}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _model_error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    return Response({"error": {"details": detail}}, status=status.HTTP_400_BAD_REQUEST)


def _save(serializer, **kwargs):
    """Save inside a savepoint so a unique-constraint clash becomes a clean 400."""
    with transaction.atomic():
        return serializer.save(**kwargs)


def _guarded(call):
    """Run a save; turn model validation and unique clashes into 400 responses."""
    try:
        return call(), None
    except DjangoValidationError as exc:
        return None, _model_error(exc)
    except IntegrityError:
        return None, _duplicate_error()


# ---- serializers ----------------------------------------------------------------


class _CompanyContacts(serializers.PrimaryKeyRelatedField):
    """A contact of the active company only (evaluated per request)."""

    def get_queryset(self):
        return Contact.objects.all()


class PropertySerializer(serializers.ModelSerializer):
    unit_count = serializers.SerializerMethodField()
    vacant_units = serializers.SerializerMethodField()
    current_owners = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = [
            "id", "name", "property_type", "tenure", "address_line", "city", "state", "country",
            "year_built", "total_area_sqm", "status", "description",
            "unit_count", "vacant_units", "current_owners",
        ]
        read_only_fields = ["id"]

    def get_unit_count(self, obj):
        return obj.units.count()

    def get_vacant_units(self, obj):
        return obj.units.filter(occupancy_status=Unit.Occupancy.VACANT).count()

    def get_current_owners(self, obj):
        return [
            {"owner": str(o.owner_id), "name": o.owner.display_name, "share_percent": str(o.share_percent)}
            for o in obj.ownerships.filter(end_date__isnull=True).select_related("owner")
        ]


class BuildingSerializer(serializers.ModelSerializer):
    unit_count = serializers.SerializerMethodField()

    class Meta:
        model = Building
        fields = ["id", "property", "name", "floors", "description", "is_active", "unit_count"]
        read_only_fields = ["id", "property"]

    def get_unit_count(self, obj):
        return obj.units.count()


class UnitSerializer(serializers.ModelSerializer):
    building = serializers.PrimaryKeyRelatedField(
        queryset=Building.objects.none(), allow_null=True, required=False
    )

    class Meta:
        model = Unit
        fields = [
            "id", "property", "building", "label", "unit_type", "floor", "bedrooms", "bathrooms",
            "area_sqm", "rent_amount", "occupancy_status", "available_from", "description",
        ]
        read_only_fields = ["id", "property"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building"].queryset = Building.objects.all()


class OwnershipSerializer(serializers.ModelSerializer):
    owner = _CompanyContacts()
    owner_name = serializers.CharField(source="owner.display_name", read_only=True)
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = PropertyOwnership
        fields = [
            "id", "property", "owner", "owner_name", "share_percent",
            "start_date", "end_date", "is_current", "notes",
        ]
        read_only_fields = ["id", "property"]


class ManagementSerializer(serializers.ModelSerializer):
    manager = _CompanyContacts()
    manager_name = serializers.CharField(source="manager.display_name", read_only=True)

    class Meta:
        model = PropertyManagement
        fields = [
            "id", "property", "manager", "manager_name", "fee_percent",
            "start_date", "end_date", "scope",
        ]
        read_only_fields = ["id", "property"]


class DocumentSerializer(serializers.ModelSerializer):
    unit = serializers.PrimaryKeyRelatedField(
        queryset=Unit.objects.none(), allow_null=True, required=False
    )

    class Meta:
        model = PropertyDocument
        fields = [
            "id", "property", "unit", "category", "title", "original_name", "content_type",
            "size_bytes", "sha256", "notes", "is_archived", "created_at",
        ]
        read_only_fields = [
            "id", "property", "original_name", "content_type", "size_bytes", "sha256", "created_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = Unit.objects.all()


class TenantProfileSerializer(serializers.ModelSerializer):
    id_number = serializers.CharField(write_only=True, required=False, allow_blank=True)
    id_number_masked = serializers.CharField(read_only=True)

    class Meta:
        model = TenantProfile
        fields = [
            "contact", "occupation", "employer", "next_of_kin_name", "next_of_kin_phone",
            "id_type", "id_number", "id_number_masked", "notes",
        ]
        read_only_fields = ["contact"]


class TenancySerializer(serializers.ModelSerializer):
    unit = serializers.PrimaryKeyRelatedField(queryset=Unit.objects.none())
    tenant = _CompanyContacts()
    tenant_name = serializers.CharField(source="tenant.display_name", read_only=True)

    class Meta:
        model = Tenancy
        fields = [
            "id", "unit", "tenant", "tenant_name", "start_date", "end_date", "rent_amount",
            "rent_frequency", "deposit_amount", "status", "end_reason", "notes", "renewed_from",
        ]
        read_only_fields = ["id", "status", "end_reason", "renewed_from"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = Unit.objects.all()


# ---- view base ------------------------------------------------------------------


class _Base(APIView):
    """Subclasses set `view_permission` (reads) and `manage_permission` (changes)."""

    view_permission = None
    manage_permission = None

    def get_permissions(self):
        code = self.view_permission if self.request.method == "GET" else self.manage_permission
        return [IsAuthenticated(), require(code)()]

    @staticmethod
    def not_found():
        return Response(status=status.HTTP_404_NOT_FOUND)


class _PropertyChildList(_Base):
    """GET/POST a collection that hangs under /properties/<id>/."""

    serializer_class = None
    model = None
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"

    def queryset(self, prop):
        return self.model.objects.filter(property=prop)

    def get(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return self.not_found()
        return Response(self.serializer_class(self.queryset(prop), many=True).data)

    def post(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return self.not_found()
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance, error = _guarded(lambda: _save(serializer, property=prop))
        if error:
            return error
        return Response(self.serializer_class(instance).data, status=status.HTTP_201_CREATED)


class _Detail(_Base):
    serializer_class = None
    model = None
    editable_fields = None  # None = all

    def get(self, request, pk):
        obj = self.model.objects.filter(pk=pk).first()
        if obj is None:
            return self.not_found()
        return Response(self.serializer_class(obj).data)

    def patch(self, request, pk):
        obj = self.model.objects.filter(pk=pk).first()
        if obj is None:
            return self.not_found()
        serializer = self.serializer_class(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated, error = _guarded(lambda: _save(serializer))
        if error:
            return error
        return Response(self.serializer_class(updated).data)


# ---- properties -----------------------------------------------------------------


class PropertyListCreateView(_Base):
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"

    def get(self, request):
        qs = Property.objects.all()
        for field in ("property_type", "status", "city", "state"):
            value = request.query_params.get(field)
            if value:
                qs = qs.filter(**{field: value})
        return Response(PropertySerializer(qs, many=True).data)

    def post(self, request):
        serializer = PropertySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance, error = _guarded(lambda: _save(serializer))
        if error:
            return error
        return Response(PropertySerializer(instance).data, status=status.HTTP_201_CREATED)


class PropertyDetailView(_Detail):
    serializer_class = PropertySerializer
    model = Property
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"


# ---- buildings ------------------------------------------------------------------


class PropertyBuildingsView(_PropertyChildList):
    serializer_class = BuildingSerializer
    model = Building


class BuildingDetailView(_Detail):
    serializer_class = BuildingSerializer
    model = Building
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"


# ---- units ----------------------------------------------------------------------


class PropertyUnitsView(_PropertyChildList):
    serializer_class = UnitSerializer
    model = Unit


class UnitListView(_Base):
    """All units of the company, for availability views: ?status=vacant&property=<id>&available=1"""

    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"

    def get(self, request):
        qs = Unit.objects.all()
        params = request.query_params
        if params.get("property"):
            qs = qs.filter(property_id=params["property"])
        if params.get("building"):
            qs = qs.filter(building_id=params["building"])
        if params.get("status"):
            qs = qs.filter(occupancy_status=params["status"])
        if params.get("unit_type"):
            qs = qs.filter(unit_type=params["unit_type"])
        if params.get("available") == "1":
            qs = qs.filter(occupancy_status=Unit.Occupancy.VACANT, property__status="active")
        return Response(UnitSerializer(qs, many=True).data)


class UnitDetailView(_Detail):
    serializer_class = UnitSerializer
    model = Unit
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"


# ---- ownership and management ---------------------------------------------------


class PropertyOwnershipsView(_PropertyChildList):
    serializer_class = OwnershipSerializer
    model = PropertyOwnership


class OwnershipDetailView(_Detail):
    serializer_class = OwnershipSerializer
    model = PropertyOwnership
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"


class PropertyManagementsView(_PropertyChildList):
    serializer_class = ManagementSerializer
    model = PropertyManagement


class ManagementDetailView(_Detail):
    serializer_class = ManagementSerializer
    model = PropertyManagement
    view_permission = "properties.property.view"
    manage_permission = "properties.property.manage"


# ---- documents ------------------------------------------------------------------

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
# extension -> (content type we serve it as, the bytes a real file of that type starts with)
ALLOWED_DOCUMENT_TYPES = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        (b"PK\x03\x04",),
    ),
}


def _inspect_upload(upload):
    """Check size, extension and real file signature. Returns (content_type, sha256)."""
    if upload.size > MAX_DOCUMENT_BYTES:
        raise DjangoValidationError({"file": "Files can be at most 10 MB."})
    if upload.size == 0:
        raise DjangoValidationError({"file": "This file is empty."})
    suffix = Path(upload.name).suffix.lower()
    if suffix not in ALLOWED_DOCUMENT_TYPES:
        raise DjangoValidationError({"file": "Upload a PDF, PNG, JPG or DOCX file."})
    content_type, signatures = ALLOWED_DOCUMENT_TYPES[suffix]
    digest = hashlib.sha256()
    first = b""
    for chunk in upload.chunks():
        if not first:
            first = chunk[:16]
        digest.update(chunk)
    if not any(first.startswith(sig) for sig in signatures):
        raise DjangoValidationError({"file": "The file's contents do not match its type."})
    upload.seek(0)
    return content_type, digest.hexdigest()


class PropertyDocumentsView(_Base):
    view_permission = "properties.document.view"
    manage_permission = "properties.document.manage"

    def get(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return self.not_found()
        qs = PropertyDocument.objects.filter(property=prop)
        if request.query_params.get("include_archived") != "1":
            qs = qs.filter(is_archived=False)
        if request.query_params.get("category"):
            qs = qs.filter(category=request.query_params["category"])
        return Response(DocumentSerializer(qs, many=True).data)

    def post(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return self.not_found()
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"error": {"details": {"file": ["Attach a file."]}}}, status=400)
        serializer = DocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def create():
            content_type, sha256 = _inspect_upload(upload)
            with transaction.atomic():
                return serializer.save(
                    property=prop,
                    file=upload,
                    original_name=Path(upload.name).name[:255],
                    content_type=content_type,
                    size_bytes=upload.size,
                    sha256=sha256,
                    uploaded_by=request.user,
                )

        document, error = _guarded(create)
        if error:
            return error
        return Response(DocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class DocumentDetailView(_Detail):
    serializer_class = DocumentSerializer
    model = PropertyDocument
    view_permission = "properties.document.view"
    manage_permission = "properties.document.manage"


class DocumentDownloadView(_Base):
    view_permission = "properties.document.view"
    manage_permission = "properties.document.manage"

    def get(self, request, pk):
        document = PropertyDocument.objects.filter(pk=pk).first()
        if document is None:
            raise Http404
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_name,
            content_type=document.content_type,
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response


# ---- tenant profiles ------------------------------------------------------------


class TenantProfileView(_Base):
    view_permission = "properties.lease.view"
    manage_permission = "properties.lease.manage"

    def get(self, request, contact_id):
        profile = TenantProfile.objects.filter(contact_id=contact_id).first()
        if profile is None:
            return self.not_found()
        return Response(TenantProfileSerializer(profile).data)

    def put(self, request, contact_id):
        contact = Contact.objects.filter(pk=contact_id).first()
        if contact is None:
            return self.not_found()
        existing = TenantProfile.objects.filter(contact=contact).first()
        serializer = TenantProfileSerializer(existing, data=request.data, partial=existing is not None)
        serializer.is_valid(raise_exception=True)
        profile, error = _guarded(lambda: _save(serializer, contact=contact))
        if error:
            return error
        return Response(
            TenantProfileSerializer(profile).data,
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )


# ---- tenancies ------------------------------------------------------------------


class TenancyListCreateView(_Base):
    view_permission = "properties.lease.view"
    manage_permission = "properties.lease.manage"

    def get(self, request):
        qs = Tenancy.objects.select_related("tenant")
        params = request.query_params
        for param, field in (("unit", "unit_id"), ("tenant", "tenant_id"), ("status", "status")):
            if params.get(param):
                qs = qs.filter(**{field: params[param]})
        return Response(TenancySerializer(qs, many=True).data)

    def post(self, request):
        serializer = TenancySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance, error = _guarded(lambda: _save(serializer))
        if error:
            return error
        return Response(TenancySerializer(instance).data, status=status.HTTP_201_CREATED)


class TenancyDetailView(_Detail):
    serializer_class = TenancySerializer
    model = Tenancy
    view_permission = "properties.lease.view"
    manage_permission = "properties.lease.manage"

    def patch(self, request, pk):
        tenancy = Tenancy.objects.filter(pk=pk).first()
        if tenancy is None:
            return self.not_found()
        # Once a tenancy has started, only notes may change: the terms are the record.
        if tenancy.status != Tenancy.Status.DRAFT and set(request.data) - {"notes"}:
            return Response(
                {"error": {"details": ["Only notes can be changed once a tenancy has started."]}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().patch(request, pk)


class _TenancyAction(_Base):
    view_permission = "properties.lease.manage"
    manage_permission = "properties.lease.manage"


class TenancyActivateView(_TenancyAction):
    def post(self, request, pk):
        tenancy, error = _guarded(lambda: services.activate_tenancy(pk))
        if error:
            return error
        return Response(TenancySerializer(tenancy).data)


class TenancyEndView(_TenancyAction):
    def post(self, request, pk):
        try:
            end_date = date.fromisoformat(str(request.data.get("end_date", "")))
        except ValueError:
            return Response({"error": {"details": {"end_date": ["Use a date like 2026-12-31."]}}}, status=400)
        reason = str(request.data.get("reason", ""))
        tenancy, error = _guarded(lambda: services.end_tenancy(
            pk, end_date=end_date, reason=reason,
            bill_final_rent=request.data.get("bill_final_rent") not in (False, "false", "0", 0),
        ))
        if error:
            return error
        return Response(TenancySerializer(tenancy).data)


class TenancyRenewView(_TenancyAction):
    """Renew on new terms: {start_date, end_date?, rent_amount?, rent_frequency?, deposit_amount?}."""

    def post(self, request, pk):
        from decimal import Decimal, InvalidOperation

        data = request.data
        try:
            start = date.fromisoformat(str(data.get("start_date", "")))
            end = date.fromisoformat(str(data["end_date"])) if data.get("end_date") else None
        except ValueError:
            return Response({"error": {"details": {"dates": ["Use dates like 2027-01-01."]}}}, status=400)
        try:
            rent = Decimal(str(data["rent_amount"])) if data.get("rent_amount") not in (None, "") else None
            deposit = Decimal(str(data["deposit_amount"])) if data.get("deposit_amount") not in (None, "") else None
        except InvalidOperation:
            return Response({"error": {"details": {"amount": ["Enter a valid amount."]}}}, status=400)
        frequency = data.get("rent_frequency") or None
        if frequency and frequency not in Tenancy.Frequency.values:
            return Response({"error": {"details": {"rent_frequency": ["Not a valid frequency."]}}}, status=400)
        new, error = _guarded(lambda: services.renew_tenancy(
            pk, start_date=start, end_date=end, rent_amount=rent, rent_frequency=frequency,
            deposit_amount=deposit, notes=str(data.get("notes") or ""),
        ))
        if error:
            return error
        return Response(TenancySerializer(new).data, status=status.HTTP_201_CREATED)


# ---- deposits -------------------------------------------------------------------


class DepositTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DepositTransaction
        fields = ["id", "kind", "amount", "transaction_date", "reason"]


def _deposit_payload(tenancy):
    from decimal import Decimal

    summary = services.deposit_summary(tenancy)
    out = {
        key: format(Decimal(value).quantize(Decimal("0.01")), "f")
        for key, value in summary.items()
        if key != "transactions"
    }
    out["transactions"] = DepositTransactionSerializer(summary["transactions"], many=True).data
    return out


class TenancyDepositView(_Base):
    view_permission = "properties.lease.view"
    manage_permission = "properties.lease.view"

    def get(self, request, pk):
        tenancy = Tenancy.objects.filter(pk=pk).first()
        if tenancy is None:
            return self.not_found()
        return Response(_deposit_payload(tenancy))


class _DepositAction(_Base):
    view_permission = "properties.payment.record"
    manage_permission = "properties.payment.record"

    def parse_date(self, request):
        raw = request.data.get("date")
        if not raw:
            return None
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            raise DjangoValidationError({"date": "Use a date like 2027-01-31."})

    def post(self, request, pk):
        tenancy = Tenancy.objects.filter(pk=pk).first()
        if tenancy is None:
            return self.not_found()
        _, error = _guarded(lambda: self.run(request, pk))
        if error:
            return error
        tenancy.refresh_from_db()
        return Response(_deposit_payload(tenancy), status=status.HTTP_201_CREATED)


class DepositReceiveView(_DepositAction):
    def run(self, request, pk):
        return services.receive_deposit(
            pk, amount=request.data.get("amount"), date=self.parse_date(request),
            account_id=request.data.get("account") or None, posted_by=request.user,
        )


class DepositDeductView(_DepositAction):
    def run(self, request, pk):
        return services.deduct_from_deposit(
            pk, amount=request.data.get("amount"), reason=str(request.data.get("reason") or ""),
            date=self.parse_date(request), income_account_id=request.data.get("income_account") or None,
            posted_by=request.user,
        )


class DepositRefundView(_DepositAction):
    def run(self, request, pk):
        return services.refund_deposit(
            pk, amount=request.data.get("amount"), date=self.parse_date(request),
            account_id=request.data.get("account") or None, posted_by=request.user,
        )
