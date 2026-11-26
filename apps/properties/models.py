from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from apps.contacts.models import ContactRole
from apps.core.models import TenantModel

OWNER_ROLES = (ContactRole.Role.PROPERTY_OWNER, ContactRole.Role.LANDLORD)


class Property(TenantModel):
    class PropertyType(models.TextChoices):
        RESIDENTIAL = "residential", "Residential"
        COMMERCIAL = "commercial", "Commercial"
        MIXED = "mixed", "Mixed use"
        LAND = "land", "Land"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SOLD = "sold", "Sold"

    name = models.CharField(max_length=200)
    property_type = models.CharField(max_length=20, choices=PropertyType.choices)
    address_line = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=2, default="NG")
    owner = models.ForeignKey(
        "contacts.Contact",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="owned_properties",
    )
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
        if self.owner_id:
            if self.organization_id and self.owner.organization_id != self.organization_id:
                raise ValidationError({"owner": "The owner must belong to the same company."})
            if not self.owner.roles.filter(role__in=OWNER_ROLES).exists():
                raise ValidationError(
                    {"owner": "Give this contact the Property Owner or Landlord role first."}
                )

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
    label = models.CharField(max_length=50)
    unit_type = models.CharField(max_length=20, choices=UnitType.choices)
    rent_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    occupancy_status = models.CharField(
        max_length=12, choices=Occupancy.choices, default=Occupancy.VACANT
    )
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
        if self.property_id and self.organization_id:
            if self.property.organization_id != self.organization_id:
                raise ValidationError({"property": "The property must belong to the same company."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
