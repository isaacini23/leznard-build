
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models

from apps.core.models import TenantModel
from apps.core.tenancy import get_current_org_id


def _prepare_tenant_instance(instance):
    """Assign the active organization and validate tenant ownership."""
    active_org_id = get_current_org_id()

    if active_org_id is None:
        raise PermissionDenied(
            "No active organization; refusing to save contact data."
        )

    if instance.organization_id is None:
        instance.organization_id = active_org_id
    elif instance.organization_id != active_org_id:
        raise PermissionDenied(
            "Cannot save a record that belongs to another organization."
        )

    instance.full_clean()


class Contact(TenantModel):
    class ContactType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        ORGANIZATION = "organization", "Organization"

    contact_type = models.CharField(
        max_length=20,
        choices=ContactType.choices,
        default=ContactType.INDIVIDUAL,
    )
    display_name = models.CharField(max_length=200)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    legal_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    tax_id = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]
        indexes = [
            models.Index(fields=["organization", "display_name"]),
            models.Index(fields=["organization", "is_active"]),
        ]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        _prepare_tenant_instance(self)
        super().save(*args, **kwargs)


class ContactRole(TenantModel):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SUPPLIER = "supplier", "Supplier"
        CONTRACTOR = "contractor", "Contractor"
        SUBCONTRACTOR = "subcontractor", "Subcontractor"
        TENANT = "tenant", "Tenant"
        LANDLORD = "landlord", "Landlord"
        PROPERTY_OWNER = "property_owner", "Property Owner"
        OTHER = "other", "Other"

    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="roles",
    )
    role = models.CharField(max_length=30, choices=Role.choices)

    class Meta:
        ordering = ["role"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact", "role"],
                name="unique_contact_role",
            ),
        ]

    def __str__(self):
        return f"{self.contact} - {self.get_role_display()}"

    def clean(self):
        super().clean()

        if (
            self.contact_id
            and self.organization_id
            and self.contact.organization_id != self.organization_id
        ):
            raise ValidationError({
                "contact": "The contact must belong to the same organization."
            })

    def save(self, *args, **kwargs):
        _prepare_tenant_instance(self)
        super().save(*args, **kwargs)


class ContactAddress(TenantModel):
    class AddressType(models.TextChoices):
        OFFICE = "office", "Office"
        BILLING = "billing", "Billing"
        SHIPPING = "shipping", "Shipping"
        SITE = "site", "Site"
        RESIDENTIAL = "residential", "Residential"
        OTHER = "other", "Other"

    contact = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="addresses",
    )
    address_type = models.CharField(
        max_length=20,
        choices=AddressType.choices,
        default=AddressType.OFFICE,
    )
    street_address = models.CharField(max_length=255)
    address_line_2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="Nigeria")
    postal_code = models.CharField(max_length=20, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["contact", "-is_default", "address_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact"],
                condition=models.Q(is_default=True),
                name="unique_default_contact_address",
            ),
        ]

    def __str__(self):
        return f"{self.contact} - {self.city}"

    def clean(self):
        super().clean()

        if (
            self.contact_id
            and self.organization_id
            and self.contact.organization_id != self.organization_id
        ):
            raise ValidationError({
                "contact": "The contact must belong to the same organization."
            })

    def save(self, *args, **kwargs):
        _prepare_tenant_instance(self)
        super().save(*args, **kwargs)
