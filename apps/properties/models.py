import builtins
import uuid
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

from apps.contacts.models import Contact, ContactRole
from apps.core.models import TenantModel

OWNER_ROLES = (ContactRole.Role.PROPERTY_OWNER, ContactRole.Role.LANDLORD)
HUNDRED = Decimal("100")


def _same_company(obj, field, other):
    if other is not None and obj.organization_id and other.organization_id != obj.organization_id:
        raise ValidationError({field: "This must belong to the same company."})


class Property(TenantModel):
    class PropertyType(models.TextChoices):
        RESIDENTIAL = "residential", "Residential"
        COMMERCIAL = "commercial", "Commercial"
        INDUSTRIAL = "industrial", "Industrial"
        MIXED = "mixed", "Mixed use"
        LAND = "land", "Land"

    class Tenure(models.TextChoices):
        FREEHOLD = "freehold", "Freehold"
        LEASEHOLD = "leasehold", "Leasehold"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SOLD = "sold", "Sold"

    name = models.CharField(max_length=200)
    property_type = models.CharField(max_length=20, choices=PropertyType.choices)
    tenure = models.CharField(max_length=10, choices=Tenure.choices, blank=True)
    address_line = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, default="NG")
    year_built = models.PositiveSmallIntegerField(null=True, blank=True)
    total_area_sqm = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "properties"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="unique_property_name_per_org")
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.total_area_sqm is not None and self.total_area_sqm <= 0:
            raise ValidationError({"total_area_sqm": "Area must be more than zero."})
        if self.year_built is not None and not 1800 <= self.year_built <= 2200:
            raise ValidationError({"year_built": "Enter a realistic year."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Building(TenantModel):
    """An optional layer between a property and its units (a block, a tower, a wing)."""

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField(max_length=150)
    floors = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["property__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "property", "name"], name="unique_building_name_per_property"
            )
        ]

    def __str__(self):
        return f"{self.property.name} / {self.name}"

    def clean(self):
        super().clean()
        _same_company(self, "property", self.property if self.property_id else None)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Unit(TenantModel):
    class UnitType(models.TextChoices):
        FLAT = "flat", "Flat / apartment"
        HOUSE = "house", "House"
        ROOM = "room", "Room"
        SHOP = "shop", "Shop"
        OFFICE = "office", "Office"
        WAREHOUSE = "warehouse", "Warehouse"
        OTHER = "other", "Other"

    class Occupancy(models.TextChoices):
        VACANT = "vacant", "Vacant"
        OCCUPIED = "occupied", "Occupied"
        MAINTENANCE = "maintenance", "Under maintenance"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    building = models.ForeignKey(
        Building, on_delete=models.PROTECT, null=True, blank=True, related_name="units"
    )
    label = models.CharField(max_length=50)
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    floor = models.CharField(max_length=20, blank=True)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    area_sqm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rent_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    occupancy_status = models.CharField(
        max_length=12, choices=Occupancy.choices, default=Occupancy.VACANT
    )
    available_from = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "property", "label"], name="unique_unit_label_per_property"
            )
        ]

    def __str__(self):
        return f"{self.property.name} / {self.label}"

    def clean(self):
        super().clean()
        if self.rent_amount is not None and self.rent_amount < Decimal("0"):
            raise ValidationError({"rent_amount": "Rent cannot be negative."})
        if self.area_sqm is not None and self.area_sqm <= 0:
            raise ValidationError({"area_sqm": "Area must be more than zero."})
        if self.property_id:
            _same_company(self, "property", self.property)
            if self.building_id:
                _same_company(self, "building", self.building)
                if self.building.property_id != self.property_id:
                    raise ValidationError({"building": "This building belongs to a different property."})
        if self.pk and self.occupancy_status != self.Occupancy.OCCUPIED:
            if Tenancy.objects.filter(
                unit_id=self.pk, status=Tenancy.Status.ACTIVE
            ).exists():
                raise ValidationError({
                    "occupancy_status": (
                        "A unit with an active tenancy must remain occupied. "
                        "End the tenancy before changing its occupancy status."
                    )
                })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PropertyOwnership(TenantModel):
    """Who owns a property, in what share, and for how long. History is kept."""

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="ownerships")
    owner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="ownerships")
    share_percent = models.DecimalField(max_digits=5, decimal_places=2, default=HUNDRED)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "-start_date"]

    def __str__(self):
        return f"{self.owner} owns {self.share_percent}% of {self.property}"

    @builtins.property  # `property` is a field name on this model
    def is_current(self):
        return self.end_date is None

    def clean(self):
        super().clean()
        if not (Decimal("0") < self.share_percent <= HUNDRED):
            raise ValidationError({"share_percent": "A share must be more than 0 and at most 100."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date cannot be before the start date."})
        if self.property_id and self.owner_id:
            _same_company(self, "property", self.property)
            _same_company(self, "owner", self.owner)
            if not self.owner.roles.filter(role__in=OWNER_ROLES).exists():
                raise ValidationError(
                    {"owner": "Give this contact the Property Owner or Landlord role first."}
                )
            if self.end_date is None:
                others = PropertyOwnership.objects.filter(property=self.property, end_date__isnull=True)
                if self.pk:
                    others = others.exclude(pk=self.pk)
                total = (others.aggregate(t=Sum("share_percent"))["t"] or Decimal("0")) + self.share_percent
                if total > HUNDRED:
                    raise ValidationError(
                        {"share_percent": f"Current ownership shares would add up to {total}%, more than 100%."}
                    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PropertyManagement(TenantModel):
    """A management arrangement: who manages a property, on what terms, and when."""

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="managements")
    manager = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="managed_properties")
    fee_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    scope = models.TextField(blank=True)

    class Meta:
        ordering = ["property__name", "-start_date"]

    def clean(self):
        super().clean()
        if self.fee_percent is not None and not (Decimal("0") <= self.fee_percent <= HUNDRED):
            raise ValidationError({"fee_percent": "A fee must be between 0 and 100 percent."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date cannot be before the start date."})
        if self.property_id and self.manager_id:
            _same_company(self, "property", self.property)
            _same_company(self, "manager", self.manager)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def _document_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    return f"property-documents/{instance.organization_id}/{uuid.uuid4().hex}{suffix}"


class PropertyDocument(TenantModel):
    """A file about a property or one of its units: title deed, C of O, survey plan, insurance..."""

    class Category(models.TextChoices):
        TITLE_DEED = "title_deed", "Title deed"
        CERTIFICATE_OF_OCCUPANCY = "certificate_of_occupancy", "Certificate of Occupancy"
        SURVEY_PLAN = "survey_plan", "Survey plan"
        BUILDING_APPROVAL = "building_approval", "Building approval"
        INSURANCE = "insurance", "Insurance"
        TENANCY_AGREEMENT = "tenancy_agreement", "Tenancy agreement"
        INSPECTION = "inspection", "Inspection report"
        OTHER = "other", "Other"

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="documents")
    unit = models.ForeignKey(
        Unit, on_delete=models.PROTECT, null=True, blank=True, related_name="documents"
    )
    category = models.CharField(max_length=30, choices=Category.choices)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to=_document_path, max_length=300)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    notes = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.property_id:
            _same_company(self, "property", self.property)
            if self.unit_id:
                _same_company(self, "unit", self.unit)
                if self.unit.property_id != self.property_id:
                    raise ValidationError({"unit": "This unit belongs to a different property."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class TenantProfile(TenantModel):
    """Extra details about a tenant, held against their Contact. Has no login or ledger access."""

    class IdType(models.TextChoices):
        NIN = "nin", "National ID number (NIN)"
        PASSPORT = "passport", "International passport"
        DRIVERS_LICENCE = "drivers_licence", "Driver's licence"
        VOTERS_CARD = "voters_card", "Voter's card"
        OTHER = "other", "Other"

    contact = models.OneToOneField(Contact, on_delete=models.PROTECT, related_name="tenant_profile")
    occupation = models.CharField(max_length=150, blank=True)
    employer = models.CharField(max_length=200, blank=True)
    next_of_kin_name = models.CharField(max_length=200, blank=True)
    next_of_kin_phone = models.CharField(max_length=30, blank=True)
    id_type = models.CharField(max_length=20, choices=IdType.choices, blank=True)
    id_number = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)

    @property
    def id_number_masked(self):
        if not self.id_number:
            return ""
        return "•" * max(len(self.id_number) - 4, 0) + self.id_number[-4:]

    def clean(self):
        super().clean()
        if self.contact_id:
            _same_company(self, "contact", self.contact)
            if not self.contact.roles.filter(role=ContactRole.Role.TENANT).exists():
                raise ValidationError({"contact": "This contact does not have the Tenant role."})
        if self.id_number and not self.id_type:
            raise ValidationError({"id_type": "Say what kind of identification this is."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Tenancy(TenantModel):
    """Who occupies a unit, from when, at what rent. Kept forever; ended, never deleted.

    This is the initial foundation. Full lease terms, rent charges and ledger
    postings are added on top of it later.
    """

    class Frequency(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        BIANNUAL = "biannual", "Every six months"
        ANNUAL = "annual", "Annual"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenancies")
    tenant = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tenancies")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    rent_amount = models.DecimalField(max_digits=14, decimal_places=2)
    rent_frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.ANNUAL)
    deposit_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    end_reason = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "tenancies"
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit"],
                condition=Q(status="active"),
                name="one_active_tenancy_per_unit",
            ),
        ]

    def __str__(self):
        return f"{self.tenant} in {self.unit} ({self.status})"

    def clean(self):
        super().clean()
        if self.rent_amount is not None and self.rent_amount < 0:
            raise ValidationError({"rent_amount": "Rent cannot be negative."})
        if self.deposit_amount is not None and self.deposit_amount < 0:
            raise ValidationError({"deposit_amount": "A deposit cannot be negative."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date cannot be before the start date."})
        if self.unit_id and self.tenant_id:
            _same_company(self, "unit", self.unit)
            _same_company(self, "tenant", self.tenant)
            if not self.tenant.roles.filter(role=ContactRole.Role.TENANT).exists():
                raise ValidationError({"tenant": "This contact does not have the Tenant role."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
