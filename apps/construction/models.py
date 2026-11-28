"""Construction projects: contract, budget, variations, costs, subcontracts and claims.
Money flows through the normal sales, purchase and payment records; these models link them
to a project so the project can be costed and reported on."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.contacts.models import Contact, ContactRole
from apps.core.models import TenantModel
from apps.core.tenancy import get_current_org_id

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _same_company(obj, field, other):
    if other is not None and obj.organization_id and other.organization_id != obj.organization_id:
        raise ValidationError({field: "This record belongs to a different company."})


class _Validated(TenantModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = get_current_org_id()
        self.full_clean()
        super().save(*args, **kwargs)


def _has_role(contact, role):
    return ContactRole.unscoped.filter(
        organization_id=contact.organization_id, contact=contact, role=role
    ).exists()


class Project(_Validated):
    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    client = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="construction_projects")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNING)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    site_address = models.CharField(max_length=300, blank=True)
    contract_value = models.DecimalField(max_digits=16, decimal_places=2, default=ZERO)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="unique_project_code"),
            models.CheckConstraint(condition=Q(contract_value__gte=0), name="project_value_not_negative"),
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    def clean(self):
        super().clean()
        if self.client_id:
            _same_company(self, "client", self.client)
            if not _has_role(self.client, ContactRole.Role.CUSTOMER):
                raise ValidationError({"client": "The client needs the Customer role."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date cannot be before the start date."})


class BudgetLine(_Validated):
    class CostType(models.TextChoices):
        LABOUR = "labour", "Labour"
        MATERIALS = "materials", "Materials"
        SUBCONTRACTORS = "subcontractors", "Subcontractors"
        EQUIPMENT = "equipment", "Equipment and plant"
        OVERHEAD = "overhead", "Site overhead"
        OTHER = "other", "Other"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="budget_lines")
    cost_type = models.CharField(max_length=15, choices=CostType.choices)
    description = models.CharField(max_length=200)
    budget_amount = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        ordering = ["project", "cost_type", "description"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "cost_type", "description"], name="unique_budget_line"
            ),
            models.CheckConstraint(condition=Q(budget_amount__gte=0), name="budget_not_negative"),
        ]

    def __str__(self):
        return f"{self.get_cost_type_display()}: {self.description}"

    def clean(self):
        super().clean()
        if self.project_id:
            _same_company(self, "project", self.project)


class Variation(_Validated):
    """A change to the contract value agreed with the client (extra work or an omission)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="variations")
    description = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=16, decimal_places=2, help_text="Negative for an omission.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    decided_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["project", "created_at"]

    def clean(self):
        super().clean()
        if self.project_id:
            _same_company(self, "project", self.project)
        if self.amount == 0:
            raise ValidationError({"amount": "A variation must change the contract value."})


class ProjectCost(_Validated):
    """Marks a purchase bill as a cost of a project (and of one budget line)."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="costs")
    purchase_bill = models.OneToOneField(
        "sales.PurchaseBill", on_delete=models.PROTECT, related_name="project_cost"
    )
    budget_line = models.ForeignKey(
        BudgetLine, on_delete=models.PROTECT, null=True, blank=True, related_name="costs"
    )

    def clean(self):
        super().clean()
        if self.project_id:
            _same_company(self, "project", self.project)
        if self.budget_line_id and self.budget_line.project_id != self.project_id:
            raise ValidationError({"budget_line": "This budget line belongs to a different project."})


class ProjectInvoice(_Validated):
    """A progress invoice billed to the client for a project."""

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="invoices")
    invoice = models.OneToOneField(
        "sales.SalesInvoice", on_delete=models.PROTECT, related_name="project_invoice"
    )
    description = models.CharField(max_length=300, blank=True)


class Subcontract(_Validated):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        TERMINATED = "terminated", "Terminated"

    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="subcontracts")
    subcontractor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="subcontracts")
    description = models.CharField(max_length=300)
    contract_sum = models.DecimalField(max_digits=16, decimal_places=2)
    retention_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5"))
    budget_line = models.ForeignKey(
        BudgetLine, on_delete=models.PROTECT, null=True, blank=True, related_name="subcontracts"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["project", "created_at"]
        constraints = [models.CheckConstraint(condition=Q(contract_sum__gt=0), name="subcontract_sum_positive")]

    def clean(self):
        super().clean()
        if self.project_id:
            _same_company(self, "project", self.project)
        if self.subcontractor_id:
            _same_company(self, "subcontractor", self.subcontractor)
            if not _has_role(self.subcontractor, ContactRole.Role.SUBCONTRACTOR):
                raise ValidationError({"subcontractor": "This contact needs the Subcontractor role."})
        if not (ZERO <= self.retention_percent <= HUNDRED):
            raise ValidationError({"retention_percent": "Retention must be between 0 and 100 percent."})
        if self.budget_line_id and self.budget_line.project_id != self.project_id:
            raise ValidationError({"budget_line": "This budget line belongs to a different project."})


class SubcontractClaim(_Validated):
    """A progress claim from a subcontractor. Certifying it creates the purchase bill for the
    gross value; the retention stays unpaid on that bill until it is released."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CERTIFIED = "certified", "Certified"
        CANCELLED = "cancelled", "Cancelled"

    subcontract = models.ForeignKey(Subcontract, on_delete=models.PROTECT, related_name="claims")
    number = models.PositiveIntegerField()
    claim_date = models.DateField()
    description = models.CharField(max_length=300, blank=True)
    gross_amount = models.DecimalField(max_digits=16, decimal_places=2)
    retention_amount = models.DecimalField(max_digits=16, decimal_places=2, default=ZERO)
    retention_released = models.DecimalField(max_digits=16, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    purchase_bill = models.OneToOneField(
        "sales.PurchaseBill", on_delete=models.PROTECT, null=True, blank=True, related_name="subcontract_claim"
    )

    class Meta:
        ordering = ["subcontract", "number"]
        constraints = [
            models.UniqueConstraint(fields=["subcontract", "number"], name="unique_claim_number"),
            models.CheckConstraint(condition=Q(gross_amount__gt=0), name="claim_gross_positive"),
        ]

    @property
    def net_payable(self):
        return self.gross_amount - self.retention_amount

    @property
    def retention_held(self):
        return self.retention_amount - self.retention_released

    def clean(self):
        super().clean()
        if self.subcontract_id:
            _same_company(self, "subcontract", self.subcontract)
