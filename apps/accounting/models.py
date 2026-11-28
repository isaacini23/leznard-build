from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TenantModel
from apps.core.tenancy import get_current_org_id


class Account(TenantModel):
    class Category(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    class AccountType(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"
        ACCOUNTS_RECEIVABLE = "accounts_receivable", "Accounts receivable"
        INVENTORY = "inventory", "Inventory"
        PREPAID_EXPENSE = "prepaid_expense", "Prepaid expense"
        FIXED_ASSET = "fixed_asset", "Property, plant and equipment"
        ACCUMULATED_DEPRECIATION = "accumulated_depreciation", "Accumulated depreciation"
        OTHER_ASSET = "other_asset", "Other asset"
        ACCOUNTS_PAYABLE = "accounts_payable", "Accounts payable"
        VAT_PAYABLE = "vat_payable", "VAT payable"
        PAYE_PAYABLE = "paye_payable", "PAYE payable"
        WITHHOLDING_TAX_PAYABLE = "withholding_tax_payable", "Withholding tax payable"
        LOAN_PAYABLE = "loan_payable", "Loan payable"
        OTHER_LIABILITY = "other_liability", "Other liability"
        OWNER_CAPITAL = "owner_capital", "Owner capital"
        RETAINED_EARNINGS = "retained_earnings", "Retained earnings"
        DRAWINGS = "drawings", "Owner drawings"
        SALES_REVENUE = "sales_revenue", "Sales revenue"
        SERVICE_REVENUE = "service_revenue", "Service revenue"
        OTHER_INCOME = "other_income", "Other income"
        COST_OF_SALES = "cost_of_sales", "Cost of sales"
        SALARIES = "salaries", "Salaries and wages"
        RENT = "rent", "Rent"
        UTILITIES = "utilities", "Utilities"
        TRANSPORT = "transport", "Transport and logistics"
        OFFICE_SUPPLIES = "office_supplies", "Office supplies"
        BANK_CHARGES = "bank_charges", "Bank charges"
        DEPRECIATION_EXPENSE = "depreciation_expense", "Depreciation expense"
        REPAIRS = "repairs", "Repairs and maintenance"
        TAX_EXPENSE = "tax_expense", "Tax expense"
        OTHER_EXPENSE = "other_expense", "Other operating expense"

    class NormalBalance(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    ACCOUNT_TYPE_CATEGORIES = {
        AccountType.CASH: Category.ASSET,
        AccountType.BANK: Category.ASSET,
        AccountType.ACCOUNTS_RECEIVABLE: Category.ASSET,
        AccountType.INVENTORY: Category.ASSET,
        AccountType.PREPAID_EXPENSE: Category.ASSET,
        AccountType.FIXED_ASSET: Category.ASSET,
        AccountType.ACCUMULATED_DEPRECIATION: Category.ASSET,
        AccountType.OTHER_ASSET: Category.ASSET,
        AccountType.ACCOUNTS_PAYABLE: Category.LIABILITY,
        AccountType.VAT_PAYABLE: Category.LIABILITY,
        AccountType.PAYE_PAYABLE: Category.LIABILITY,
        AccountType.WITHHOLDING_TAX_PAYABLE: Category.LIABILITY,
        AccountType.LOAN_PAYABLE: Category.LIABILITY,
        AccountType.OTHER_LIABILITY: Category.LIABILITY,
        AccountType.OWNER_CAPITAL: Category.EQUITY,
        AccountType.RETAINED_EARNINGS: Category.EQUITY,
        AccountType.DRAWINGS: Category.EQUITY,
        AccountType.SALES_REVENUE: Category.INCOME,
        AccountType.SERVICE_REVENUE: Category.INCOME,
        AccountType.OTHER_INCOME: Category.INCOME,
        AccountType.COST_OF_SALES: Category.EXPENSE,
        AccountType.SALARIES: Category.EXPENSE,
        AccountType.RENT: Category.EXPENSE,
        AccountType.UTILITIES: Category.EXPENSE,
        AccountType.TRANSPORT: Category.EXPENSE,
        AccountType.OFFICE_SUPPLIES: Category.EXPENSE,
        AccountType.BANK_CHARGES: Category.EXPENSE,
        AccountType.DEPRECIATION_EXPENSE: Category.EXPENSE,
        AccountType.REPAIRS: Category.EXPENSE,
        AccountType.TAX_EXPENSE: Category.EXPENSE,
        AccountType.OTHER_EXPENSE: Category.EXPENSE,
    }

    CATEGORY_NORMAL_BALANCES = {
        Category.ASSET: NormalBalance.DEBIT,
        Category.LIABILITY: NormalBalance.CREDIT,
        Category.EQUITY: NormalBalance.CREDIT,
        Category.INCOME: NormalBalance.CREDIT,
        Category.EXPENSE: NormalBalance.DEBIT,
    }
    ACCOUNT_TYPE_NORMAL_BALANCE_OVERRIDES = {
        AccountType.ACCUMULATED_DEPRECIATION: NormalBalance.CREDIT,
        AccountType.DRAWINGS: NormalBalance.DEBIT,
    }

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=Category.choices)
    account_type = models.CharField(max_length=40, choices=AccountType.choices)
    normal_balance = models.CharField(max_length=6, choices=NormalBalance.choices)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
    )
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="unique_account_code_per_org",
            )
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        super().clean()
        expected_category = self.ACCOUNT_TYPE_CATEGORIES.get(self.account_type)
        if expected_category and self.category != expected_category:
            raise ValidationError(
                {"category": "The selected account type does not belong to this category."}
            )

        expected_normal_balance = self.ACCOUNT_TYPE_NORMAL_BALANCE_OVERRIDES.get(
            self.account_type,
            self.CATEGORY_NORMAL_BALANCES.get(self.category),
        )
        if expected_normal_balance and self.normal_balance != expected_normal_balance:
            raise ValidationError(
                {"normal_balance": (
                    f"Accounts of this type must have a "
                    f"{expected_normal_balance} normal balance."
                )}
            )

        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "An account cannot be its own parent."})
            if self.organization_id and self.parent.organization_id != self.organization_id:
                raise ValidationError(
                    {"parent": "Parent and child accounts must belong to the same organization."}
                )

            ancestor = self.parent
            while ancestor is not None:
                if self.pk and ancestor.pk == self.pk:
                    raise ValidationError(
                        {"parent": "This parent would create an account hierarchy cycle."}
                    )
                ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        active_org_id = get_current_org_id()
        if self.organization_id is None and active_org_id is not None:
            self.organization_id = active_org_id

        if self.pk and not self._state.adding:
            existing = type(self).unscoped.filter(pk=self.pk).first()
            if existing and existing.is_system:
                protected_fields = (
                    "code", "category", "account_type", "normal_balance",
                    "parent_id", "is_system",
                )
                if any(
                    getattr(existing, field) != getattr(self, field)
                    for field in protected_fields
                ):
                    raise ValidationError(
                        "The structure of a system account cannot be changed."
                    )

            if existing and not existing.is_system and existing.journal_lines.exists():
                locked_fields = (
                    "code", "category", "account_type", "normal_balance", "parent_id",
                )
                if any(
                    getattr(existing, field) != getattr(self, field)
                    for field in locked_fields
                ):
                    raise ValidationError(
                        "An account with journal entries can only be renamed or deactivated."
                    )

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValidationError("System accounts cannot be deleted.")
        return super().delete(*args, **kwargs)



class JournalEntry(TenantModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    entry_date = models.DateField()
    reference = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=255)
    currency = models.CharField(max_length=3)
    base_currency = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(max_digits=24, decimal_places=10, default=1)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_journal_entries",
    )
    reversal_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversal_entry",
    )

    class Meta:
        ordering = ["-entry_date", "-created_at"]

    def __str__(self):
        return self.reference or f"Journal {self.pk}"

    def clean(self):
        super().clean()
        if self.currency:
            self.currency = self.currency.upper()
        if self.base_currency:
            self.base_currency = self.base_currency.upper()
        if self.currency and len(self.currency) != 3:
            raise ValidationError({"currency": "Use a three-letter currency code."})
        if self.base_currency and len(self.base_currency) != 3:
            raise ValidationError({"base_currency": "Use a three-letter currency code."})
        if self.exchange_rate is None or self.exchange_rate <= 0:
            raise ValidationError({"exchange_rate": "Exchange rate must be greater than zero."})
        if self.currency == self.base_currency and self.exchange_rate != 1:
            raise ValidationError(
                {"exchange_rate": "The exchange rate must be 1 for the base currency."}
            )
        if self.reversal_of_id and self.organization_id:
            if self.reversal_of.organization_id != self.organization_id:
                raise ValidationError(
                    {"reversal_of": "A journal can only reverse an entry from the same company."}
                )

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            existing = type(self).unscoped.filter(pk=self.pk).first()
            if existing and existing.status != self.Status.DRAFT:
                raise ValidationError("Posted or reversed journal entries cannot be edited.")
        self.full_clean()
        super().save(*args, **kwargs)


class JournalLine(TenantModel):
    entry = models.ForeignKey(
        JournalEntry, on_delete=models.PROTECT, related_name="lines"
    )
    account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="journal_lines"
    )
    description = models.CharField(max_length=255, blank=True)
    debit_amount = models.DecimalField(max_digits=20, decimal_places=4, default=0)
    credit_amount = models.DecimalField(max_digits=20, decimal_places=4, default=0)
    base_debit_amount = models.DecimalField(max_digits=28, decimal_places=10, default=0)
    base_credit_amount = models.DecimalField(max_digits=28, decimal_places=10, default=0)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.account} ({self.entry})"

    def clean(self):
        super().clean()
        if self.entry_id and self.organization_id != self.entry.organization_id:
            raise ValidationError({"entry": "Journal line and entry must belong to the same company."})
        if self.account_id and self.organization_id != self.account.organization_id:
            raise ValidationError({"account": "Account must belong to the same company as the journal."})
        if self.entry_id and self.entry.status != JournalEntry.Status.DRAFT:
            raise ValidationError("Lines on posted or reversed entries cannot be changed.")
        debit = self.debit_amount or 0
        credit = self.credit_amount or 0
        if debit < 0 or credit < 0:
            raise ValidationError("Debit and credit amounts cannot be negative.")
        if (debit > 0) == (credit > 0):
            raise ValidationError("Each journal line must contain either a debit or a credit, not both.")
        if debit == 0 and credit == 0:
            raise ValidationError("A journal line must have a positive debit or credit amount.")

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            existing = type(self).unscoped.filter(pk=self.pk).first()
            if existing and existing.entry.status != JournalEntry.Status.DRAFT:
                raise ValidationError("Lines on posted or reversed entries cannot be edited.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.entry.status != JournalEntry.Status.DRAFT:
            raise ValidationError("Lines on posted or reversed entries cannot be deleted.")
        return super().delete(*args, **kwargs)


class AccountingPeriod(TenantModel):
    """A date range that can be open or closed to posting.

    Periods are created on demand (calendar months) the first time something is
    posted into them. Posting into a closed period is refused.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    name = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reopened_at = models.DateTimeField(null=True, blank=True)
    reopened_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "start_date"], name="one_period_per_start_date"
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="period_end_not_before_start",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.status})"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValidationError({"end_date": "The period cannot end before it starts."})
            overlapping = type(self).unscoped.filter(
                organization_id=self.organization_id or get_current_org_id(),
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError("This period overlaps another accounting period.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
