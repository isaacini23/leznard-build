from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TenantModel
from apps.core.tenancy import get_current_org_id
from apps.contacts.models import ContactRole


class Payment(TenantModel):
    class Type(models.TextChoices):
        CUSTOMER_RECEIPT = "customer_receipt", "Customer receipt"
        SUPPLIER_PAYMENT = "supplier_payment", "Supplier payment"

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        CARD = "card", "Card"
        CHEQUE = "cheque", "Cheque"
        DIRECT_DEBIT = "direct_debit", "Direct debit"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    payment_type = models.CharField(max_length=24, choices=Type.choices)
    payment_date = models.DateField()
    contact = models.ForeignKey("contacts.Contact", on_delete=models.PROTECT, related_name="payments")
    account = models.ForeignKey("accounting.Account", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=20, decimal_places=4)
    currency = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(max_digits=24, decimal_places=10, default=1)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.BANK_TRANSFER)
    reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry", on_delete=models.PROTECT,
        null=True, blank=True, related_name="payment",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-payment_date", "-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
            models.Index(fields=["organization", "payment_type", "payment_date"]),
        ]

    def __str__(self):
        return self.reference or f"{self.get_payment_type_display()} {self.pk}"

    def clean(self):
        super().clean()
        if self.currency:
            self.currency = self.currency.upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Use a three-letter currency code."})
        if self.exchange_rate is None or self.exchange_rate <= 0:
            raise ValidationError({"exchange_rate": "Exchange rate must be greater than zero."})
        if self.organization_id and self.currency == self.organization.base_currency.upper() and self.exchange_rate != 1:
            raise ValidationError({"exchange_rate": "Exchange rate must be 1 for the company's base currency."})
        if self.contact_id and self.organization_id:
            if self.contact.organization_id != self.organization_id:
                raise ValidationError({"contact": "Contact must belong to this organization."})
            required_role = (
                ContactRole.Role.CUSTOMER
                if self.payment_type == self.Type.CUSTOMER_RECEIPT
                else ContactRole.Role.SUPPLIER
            )
            if not ContactRole.unscoped.filter(
                organization_id=self.organization_id, contact_id=self.contact_id, role=required_role
            ).exists():
                raise ValidationError({"contact": f"Contact must have the {required_role} role."})
            if not self.contact.is_active:
                raise ValidationError({"contact": "Inactive contacts cannot receive payments."})
        if self.account_id and self.organization_id:
            if self.account.organization_id != self.organization_id:
                raise ValidationError({"account": "Payment account must belong to this organization."})
            if (
                self.account.category != self.account.Category.ASSET
                or self.account.account_type not in (
                    self.account.AccountType.CASH, self.account.AccountType.BANK
                )
                or not self.account.is_active
            ):
                raise ValidationError({"account": "Choose an active cash or bank account."})

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = get_current_org_id()
        if self.organization_id is None:
            raise PermissionDenied("No active organization; refusing to save a payment.")
        if self.pk and not self._state.adding:
            existing = type(self).unscoped.get(pk=self.pk)
            if existing.status != self.Status.DRAFT:
                raise ValidationError("Posted payments cannot be edited.")
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def allocated_amount(self):
        return sum(
            (row.amount for row in self.allocations.filter(payment__status=self.Status.DRAFT)),
            Decimal("0"),
        )

    @property
    def unallocated_amount(self):
        return self.amount - self.allocated_amount


class PaymentAllocation(TenantModel):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    sales_invoice = models.ForeignKey(
        "sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True,
        related_name="payment_allocations",
    )
    purchase_bill = models.ForeignKey(
        "sales.PurchaseBill", on_delete=models.PROTECT, null=True, blank=True,
        related_name="payment_allocations",
    )
    amount = models.DecimalField(max_digits=20, decimal_places=4)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0), name="payment_allocation_amount_positive"
            ),
            models.CheckConstraint(
                condition=(
                    Q(sales_invoice__isnull=False, purchase_bill__isnull=True)
                    | Q(sales_invoice__isnull=True, purchase_bill__isnull=False)
                ),
                name="payment_allocation_one_document",
            ),
            models.UniqueConstraint(
                fields=["payment", "sales_invoice"],
                condition=Q(sales_invoice__isnull=False),
                name="unique_payment_invoice_allocation",
            ),
            models.UniqueConstraint(
                fields=["payment", "purchase_bill"],
                condition=Q(purchase_bill__isnull=False),
                name="unique_payment_bill_allocation",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.payment_id:
            return
        if self.payment.status != Payment.Status.DRAFT:
            raise ValidationError("Allocations on posted payments cannot be changed.")
        if self.organization_id != self.payment.organization_id:
            raise ValidationError({"payment": "Allocation and payment must belong to the same organization."})
        if bool(self.sales_invoice_id) == bool(self.purchase_bill_id):
            raise ValidationError("Choose exactly one invoice or purchase bill.")
        if self.sales_invoice_id:
            document = self.sales_invoice
            if self.payment.payment_type != Payment.Type.CUSTOMER_RECEIPT:
                raise ValidationError({"sales_invoice": "Customer invoices require a customer receipt."})
            if document.organization_id != self.organization_id:
                raise ValidationError({"sales_invoice": "Invoice must belong to this organization."})
            if document.customer_id != self.payment.contact_id:
                raise ValidationError({"sales_invoice": "Invoice customer must match the payment contact."})
        else:
            document = self.purchase_bill
            if self.payment.payment_type != Payment.Type.SUPPLIER_PAYMENT:
                raise ValidationError({"purchase_bill": "Purchase bills require a supplier payment."})
            if document.organization_id != self.organization_id:
                raise ValidationError({"purchase_bill": "Bill must belong to this organization."})
            if document.supplier_id != self.payment.contact_id:
                raise ValidationError({"purchase_bill": "Bill supplier must match the payment contact."})
        if document.status != document.Status.POSTED:
            raise ValidationError("Payments can only be allocated to posted invoices or bills.")
        if document.currency.upper() != self.payment.currency.upper():
            raise ValidationError({"amount": "Payment and document currencies must match."})
        existing = type(self).unscoped.filter(payment_id=self.payment_id)
        if self.pk:
            existing = existing.exclude(pk=self.pk)
        allocated = sum((item.amount for item in existing), Decimal("0"))
        if allocated + self.amount > self.payment.amount:
            raise ValidationError({"amount": "Allocations cannot exceed the payment amount."})

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = get_current_org_id()
        if self.organization_id is None:
            raise PermissionDenied("No active organization; refusing to save a payment allocation.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.payment.status != Payment.Status.DRAFT:
            raise ValidationError("Allocations on posted payments cannot be deleted.")
        return super().delete(*args, **kwargs)
