from django.db import transaction

from apps.core.tenancy import organization_context

from .models import Account


# A practical starter chart for Nigerian businesses, intended to be customized.
DEFAULT_CHART_OF_ACCOUNTS = [
    ("1000", "Cash on Hand", Account.Category.ASSET, Account.AccountType.CASH, Account.NormalBalance.DEBIT),
    ("1010", "Bank Current Account", Account.Category.ASSET, Account.AccountType.BANK, Account.NormalBalance.DEBIT),
    ("1100", "Accounts Receivable", Account.Category.ASSET, Account.AccountType.ACCOUNTS_RECEIVABLE, Account.NormalBalance.DEBIT),
    ("1200", "Inventory", Account.Category.ASSET, Account.AccountType.INVENTORY, Account.NormalBalance.DEBIT),
    ("1300", "Prepaid Expenses", Account.Category.ASSET, Account.AccountType.PREPAID_EXPENSE, Account.NormalBalance.DEBIT),
    ("1500", "Property, Plant and Equipment", Account.Category.ASSET, Account.AccountType.FIXED_ASSET, Account.NormalBalance.DEBIT),
    ("1590", "Accumulated Depreciation", Account.Category.ASSET, Account.AccountType.ACCUMULATED_DEPRECIATION, Account.NormalBalance.CREDIT),
    ("1900", "Other Assets", Account.Category.ASSET, Account.AccountType.OTHER_ASSET, Account.NormalBalance.DEBIT),
    ("2000", "Accounts Payable", Account.Category.LIABILITY, Account.AccountType.ACCOUNTS_PAYABLE, Account.NormalBalance.CREDIT),
    ("2100", "VAT Payable", Account.Category.LIABILITY, Account.AccountType.VAT_PAYABLE, Account.NormalBalance.CREDIT),
    ("2110", "PAYE Payable", Account.Category.LIABILITY, Account.AccountType.PAYE_PAYABLE, Account.NormalBalance.CREDIT),
    ("2120", "Withholding Tax Payable", Account.Category.LIABILITY, Account.AccountType.WITHHOLDING_TAX_PAYABLE, Account.NormalBalance.CREDIT),
    ("2200", "Loans Payable", Account.Category.LIABILITY, Account.AccountType.LOAN_PAYABLE, Account.NormalBalance.CREDIT),
    ("2900", "Other Liabilities", Account.Category.LIABILITY, Account.AccountType.OTHER_LIABILITY, Account.NormalBalance.CREDIT),
    ("3000", "Owner Capital", Account.Category.EQUITY, Account.AccountType.OWNER_CAPITAL, Account.NormalBalance.CREDIT),
    ("3100", "Retained Earnings", Account.Category.EQUITY, Account.AccountType.RETAINED_EARNINGS, Account.NormalBalance.CREDIT),
    ("3200", "Owner Drawings", Account.Category.EQUITY, Account.AccountType.DRAWINGS, Account.NormalBalance.DEBIT),
    ("4000", "Sales Revenue", Account.Category.INCOME, Account.AccountType.SALES_REVENUE, Account.NormalBalance.CREDIT),
    ("4100", "Service Revenue", Account.Category.INCOME, Account.AccountType.SERVICE_REVENUE, Account.NormalBalance.CREDIT),
    ("4900", "Other Income", Account.Category.INCOME, Account.AccountType.OTHER_INCOME, Account.NormalBalance.CREDIT),
    ("5000", "Cost of Sales", Account.Category.EXPENSE, Account.AccountType.COST_OF_SALES, Account.NormalBalance.DEBIT),
    ("6000", "Salaries and Wages", Account.Category.EXPENSE, Account.AccountType.SALARIES, Account.NormalBalance.DEBIT),
    ("6100", "Rent", Account.Category.EXPENSE, Account.AccountType.RENT, Account.NormalBalance.DEBIT),
    ("6200", "Utilities", Account.Category.EXPENSE, Account.AccountType.UTILITIES, Account.NormalBalance.DEBIT),
    ("6300", "Transport and Logistics", Account.Category.EXPENSE, Account.AccountType.TRANSPORT, Account.NormalBalance.DEBIT),
    ("6400", "Office Supplies", Account.Category.EXPENSE, Account.AccountType.OFFICE_SUPPLIES, Account.NormalBalance.DEBIT),
    ("6500", "Bank Charges", Account.Category.EXPENSE, Account.AccountType.BANK_CHARGES, Account.NormalBalance.DEBIT),
    ("6600", "Depreciation Expense", Account.Category.EXPENSE, Account.AccountType.DEPRECIATION_EXPENSE, Account.NormalBalance.DEBIT),
    ("6700", "Repairs and Maintenance", Account.Category.EXPENSE, Account.AccountType.REPAIRS, Account.NormalBalance.DEBIT),
    ("6800", "Tax Expense", Account.Category.EXPENSE, Account.AccountType.TAX_EXPENSE, Account.NormalBalance.DEBIT),
    ("6900", "Other Operating Expenses", Account.Category.EXPENSE, Account.AccountType.OTHER_EXPENSE, Account.NormalBalance.DEBIT),
]


@transaction.atomic
def create_default_chart_of_accounts(organization):
    """Create the starter chart once for an organization; safe to call again."""
    created_accounts = []
    with organization_context(organization.pk):
        for code, name, category, account_type, normal_balance in DEFAULT_CHART_OF_ACCOUNTS:
            account, created = Account.unscoped.get_or_create(
                organization=organization,
                code=code,
                defaults={
                    "name": name,
                    "category": category,
                    "account_type": account_type,
                    "normal_balance": normal_balance,
                    "is_system": True,
                },
            )
            if created:
                created_accounts.append(account)
    return created_accounts
