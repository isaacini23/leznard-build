from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TenantModel
from apps.core.tenancy import get_current_org_id


MONEY = Decimal("0.0001")
ZERO = Decimal("0")


def money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


class FinancialDocument(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        VOID = "void", "Void"

    number = models.CharField(max_length=50)
    document_date = models.DateField()
    due_date = models.DateField()
    currency = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(max_digits=24, decimal_places=10, default=1)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry", on_delete=models.PROTECT,
        null=True, blank=True, related_name="%(class)s_document",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.document_date and self.due_date and self.due_date < self.document_date:
            raise ValidationError({"due_date": "Due date cannot be before document date."})
        if self.currency:
            self.currency = self.currency.upper()
            if len(self.currency) != 3:
                raise ValidationError({"currency": "Use a three-letter currency code."})
        if self.exchange_rate is None or self.exchange_rate <= 0:
            raise ValidationError({"exchange_rate": "Exchange rate must be greater than zero."})
        if self.organization_id and self.currency == self.organization.base_currency.upper() and self.exchange_rate != 1:
            raise ValidationError({"exchange_rate": "Exchange rate must be 1 for the company's base currency."})

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = get_current_org_id()
        if self.pk and not self._state.adding:
            old = type(self).unscoped.get(pk=self.pk)
            if old.status != self.Status.DRAFT:
                raise ValidationError("Posted or void documents cannot be edited.")
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def subtotal(self):
        return money(sum((line.net_amount for line in self.lines.all()), ZERO))

    @property
    def tax_total(self):
        return money(sum((line.tax_amount for line in self.lines.all()), ZERO))

    @property
    def total(self):
        return money(self.subtotal + self.tax_total)


class SalesInvoice(FinancialDocument):
    customer = models.ForeignKey(
        "contacts.Contact", on_delete=models.PROTECT, related_name="sales_invoices"
    )

    class Meta:
        ordering = ["-document_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="sales_invoice_number_per_org")
        ]

    def clean(self):
        super().clean()
        if self.customer_id and self.organization_id and self.customer.organization_id != self.organization_id:
            raise ValidationError({"customer": "Customer must belong to this organization."})
        if self.customer_id and not self.customer.is_active:
            raise ValidationError({"customer": "Inactive contacts cannot be used on new invoices."})

    def __str__(self):
        return self.number


class SalesInvoiceLine(TenantModel):
    invoice = models.ForeignKey(SalesInvoice, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=20, decimal_places=4)
    discount_amount = models.DecimalField(max_digits=20, decimal_places=4, default=ZERO)
    tax_amount = models.DecimalField(max_digits=20, decimal_places=4, default=ZERO)
    revenue_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="sales_invoice_lines"
    )
    tax_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, null=True, blank=True,
        related_name="sales_invoice_tax_lines",
    )

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def net_amount(self):
        return money(self.quantity * self.unit_price - self.discount_amount)

    def clean(self):
        super().clean()
        if self.invoice_id and self.organization_id != self.invoice.organization_id:
            raise ValidationError({"invoice": "Invoice line must belong to the same organization."})
        if self.invoice_id and self.invoice.status != SalesInvoice.Status.DRAFT:
            raise ValidationError("Lines on posted invoices cannot be changed.")
        if self.revenue_account_id and self.organization_id != self.revenue_account.organization_id:
            raise ValidationError({"revenue_account": "Revenue account must belong to this organization."})
        if self.revenue_account_id and self.revenue_account.category != "income":
            raise ValidationError({"revenue_account": "Select an income account."})
        if self.tax_amount > 0 and not self.tax_account_id:
            raise ValidationError({"tax_account": "A tax liability account is required when tax is charged."})
        if self.tax_account_id and self.organization_id != self.tax_account.organization_id:
            raise ValidationError({"tax_account": "Tax account must belong to this organization."})
        if self.tax_account_id and self.tax_account.category != "liability":
            raise ValidationError({"tax_account": "Select a liability account for sales tax."})
        if self.quantity <= 0 or self.unit_price < 0 or self.discount_amount < 0 or self.tax_amount < 0:
            raise ValidationError("Quantity must be positive and monetary amounts cannot be negative.")
        if self.discount_amount > self.quantity * self.unit_price:
            raise ValidationError({"discount_amount": "Discount cannot exceed the line's gross amount."})

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = get_current_org_id()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.status != SalesInvoice.Status.DRAFT:
            raise ValidationError("Lines on posted invoices cannot be deleted.")
        return super().delete(*args, **kwargs)


class PurchaseBill(FinancialDocument):
    supplier = models.ForeignKey(
        "contacts.Contact", on_delete=models.PROTECT, related_name="purchase_bills"
    )
    supplier_reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-document_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "number"], name="purchase_bill_number_per_org")
        ]

    def clean(self):
        super().clean()
        if self.supplier_id and self.organization_id and self.supplier.organization_id != self.organization_id:
            raise ValidationError({"supplier": "Supplier must belong to this organization."})
        if self.supplier_id and not self.supplier.is_active:
            raise ValidationError({"supplier": "Inactive contacts cannot be used on new bills."})

    def __str__(self):
        return self.number


class PurchaseBillLine(TenantModel):
    bill = models.ForeignKey(PurchaseBill, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    unit_price = models.DecimalField(max_digits=20, decimal_places=4)
    discount_amount = models.DecimalField(max_digits=20, decimal_places=4, default=ZERO)
    tax_amount = models.DecimalField(max_digits=20, decimal_places=4, default=ZERO)
    expense_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, related_name="purchase_bill_lines"
    )
    tax_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, null=True, blank=True,
        related_name="purchase_bill_tax_lines",
    )

    class Meta:
        ordering = ["created_at", "id"]

    @property
    def net_amount(self):
        return money(self.quantity * self.unit_price - self.discount_amount)

    def clean(self):
        super().clean()
        if self.bill_id and self.organization_id != self.bill.organization_id:
            raise ValidationError({"bill": "Bill line must belong to the same organization."})
        if self.bill_id and self.bill.status != PurchaseBill.Status.DRAFT:
            raise ValidationError("Lines on posted bills cannot be changed.")
        if self.expense_account_id and self.organization_id != self.expense_account.organization_id:
            raise ValidationError({"expense_account": "Expense account must belong to this organization."})
        if self.expense_account_id and self.expense_account.category != "expense":
            raise ValidationError({"expense_account": "Select an expense account."})
        if self.tax_amount > 0 and not self.tax_account_id:
            raise ValidationError({"tax_account": "Select an input-tax asset account when tax is recorded."})
        if self.tax_account_id and self.organization_id != self.tax_account.organization_id:
            raise ValidationError({"tax_account": "Tax account must belong to this organization."})
        if self.tax_account_id and self.tax_account.category != "asset":
            raise ValidationError({"tax_account": "Input tax must be posted to an asset account."})
        if self.quantity <= 0 or self.unit_price < 0 or self.discount_amount < 0 or self.tax_amount < 0:
            raise ValidationError("Quantity must be positive and monetary amounts cannot be negative.")
        if self.discount_amount > self.quantity * self.unit_price:
            raise ValidationError({"discount_amount": "Discount cannot exceed the line's gross amount."})

    def save(self, *args, **kwargs):
        if self.organization_id is None:
            self.organization_id = get_current_org_id()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.bill.status != PurchaseBill.Status.DRAFT:
            raise ValidationError("Lines on posted bills cannot be deleted.")
        return super().delete(*args, **kwargs)
