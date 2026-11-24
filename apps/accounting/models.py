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

        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_system:
            raise ValidationError("System accounts cannot be deleted.")
        return super().delete(*args, **kwargs)
