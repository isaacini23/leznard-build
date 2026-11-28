from django.db import transaction
from django.db.models import Sum

from apps.core.tenancy import organization_context

from .periods import assert_period_open
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



from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import JournalEntry, JournalLine


BASE_AMOUNT_QUANTUM = Decimal("0.0000000001")


def _to_decimal(value, field_name):
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise ValidationError({field_name: "Enter a valid decimal amount."}) from exc
    if not amount.is_finite():
        raise ValidationError({field_name: "Amount must be a finite number."})
    return amount


def _base_amount(amount, exchange_rate):
    return (amount * exchange_rate).quantize(
        BASE_AMOUNT_QUANTUM, rounding=ROUND_HALF_UP
    )


@transaction.atomic
def create_journal_entry(
    organization,
    *,
    entry_date,
    description,
    lines,
    currency=None,
    exchange_rate=None,
    reference="",
    reversal_of=None,
    is_year_end_closing=False,
    closing_period_end=None,
):
    """Create a draft journal and its lines within the supplied organization."""
    base_currency = organization.base_currency.upper()
    currency = (currency or base_currency).upper()
    if currency == base_currency:
        rate = Decimal("1")
    else:
        if exchange_rate is None:
            raise ValidationError(
                {"exchange_rate": "An exchange rate is required for foreign-currency entries."}
            )
        rate = _to_decimal(exchange_rate, "exchange_rate")
        if rate <= 0:
            raise ValidationError({"exchange_rate": "Exchange rate must be greater than zero."})

    with organization_context(organization.pk):
        entry = JournalEntry(
            organization=organization,
            entry_date=entry_date,
            reference=reference,
            is_year_end_closing=is_year_end_closing,
            closing_period_end=closing_period_end,
            description=description,
            currency=currency,
            base_currency=base_currency,
            exchange_rate=rate,
            reversal_of=reversal_of,
        )
        entry.save()

        for item in lines:
            account_value = item.get("account")
            if isinstance(account_value, Account):
                account = Account.unscoped.get(pk=account_value.pk)
            else:
                account = Account.unscoped.get(pk=account_value)

            debit = _to_decimal(item.get("debit", 0), "debit")
            credit = _to_decimal(item.get("credit", 0), "credit")
            base_debit = _base_amount(debit, rate) if debit else Decimal("0")
            base_credit = _base_amount(credit, rate) if credit else Decimal("0")

            line = JournalLine(
                organization=organization,
                entry=entry,
                account=account,
                description=item.get("description", ""),
                cash_flow_category=item.get("cash_flow_category", ""),
                debit_amount=debit,
                credit_amount=credit,
                base_debit_amount=base_debit,
                base_credit_amount=base_credit,
            )
            line.save()

    return entry


@transaction.atomic
def post_journal_entry(entry, *, posted_by=None):
    """Validate and permanently post a balanced draft journal."""
    with organization_context(entry.organization_id):
        locked = JournalEntry.unscoped.select_for_update().get(pk=entry.pk)
        if locked.status != JournalEntry.Status.DRAFT:
            raise ValidationError("Only draft journal entries can be posted.")
        assert_period_open(locked.entry_date)

        lines = list(
            JournalLine.unscoped.select_related("account").filter(entry=locked)
        )
        if len(lines) < 2:
            raise ValidationError("A journal entry must contain at least two lines.")

        if not any(line.debit_amount > 0 for line in lines):
            raise ValidationError("A journal entry must contain at least one debit.")
        if not any(line.credit_amount > 0 for line in lines):
            raise ValidationError("A journal entry must contain at least one credit.")

        for line in lines:
            if line.account.organization_id != locked.organization_id:
                raise ValidationError("Every journal account must belong to this company.")
            if not line.account.is_active:
                raise ValidationError(
                    {"account": f"Account {line.account.code} is inactive and cannot be posted to."}
                )

        if locked.is_year_end_closing:
            from datetime import date, timedelta

            from apps.organizations.models import Organization

            organization = Organization.objects.get(pk=locked.organization_id)
            fiscal_start_month = organization.fiscal_year_start_month
            next_fiscal_start = date(
                locked.closing_period_end.year
                + (locked.closing_period_end.month >= fiscal_start_month),
                fiscal_start_month,
                1,
            )
            if locked.closing_period_end != next_fiscal_start - timedelta(days=1):
                raise ValidationError(
                    {"closing_period_end": "The selected date is not the end of a fiscal year."}
                )

            fiscal_year = (
                locked.closing_period_end.year
                if locked.closing_period_end.month >= fiscal_start_month
                else locked.closing_period_end.year - 1
            )
            fiscal_year_start = date(fiscal_year, fiscal_start_month, 1)

            active_close = JournalEntry.unscoped.filter(
                organization_id=locked.organization_id,
                is_year_end_closing=True,
                closing_period_end=locked.closing_period_end,
                status=JournalEntry.Status.POSTED,
                reversal_of__isnull=True,
            ).exclude(pk=locked.pk)
            if locked.reversal_of_id:
                active_close = active_close.exclude(pk=locked.reversal_of_id)
            if active_close.exists():
                raise ValidationError(
                    {"closing_period_end": "This fiscal year already has an active closing journal."}
                )

            retained_lines = [
                line for line in lines
                if line.account.account_type == Account.AccountType.RETAINED_EARNINGS
            ]
            profit_loss_lines = [
                line for line in lines
                if line.account.category in (
                    Account.Category.INCOME, Account.Category.EXPENSE
                )
            ]
            if not retained_lines or not profit_loss_lines:
                raise ValidationError(
                    "A year-end closing journal must transfer profit or loss "
                    "between income/expense accounts and retained earnings."
                )
            if len(retained_lines) + len(profit_loss_lines) != len(lines):
                raise ValidationError(
                    "A year-end closing journal may contain only income, expense "
                    "and retained-earnings accounts."
                )

            if locked.reversal_of_id:
                original = JournalEntry.unscoped.get(pk=locked.reversal_of_id)
                if (
                    not original.is_year_end_closing
                    or original.closing_period_end != locked.closing_period_end
                ):
                    raise ValidationError(
                        "A closing reversal must reference a closing journal for the same fiscal year."
                    )
                original_totals = {}
                for line in JournalLine.unscoped.filter(entry=original):
                    original_totals[line.account_id] = (
                        original_totals.get(line.account_id, Decimal("0"))
                        + line.base_debit_amount - line.base_credit_amount
                    )
                reversal_totals = {}
                for line in lines:
                    reversal_totals[line.account_id] = (
                        reversal_totals.get(line.account_id, Decimal("0"))
                        + line.base_debit_amount - line.base_credit_amount
                    )
                expected_reversal = {
                    account_id: -amount for account_id, amount in original_totals.items()
                    if amount
                }
                actual_reversal = {
                    account_id: amount for account_id, amount in reversal_totals.items()
                    if amount
                }
                if actual_reversal != expected_reversal:
                    raise ValidationError(
                        "A closing reversal must exactly reverse the original closing journal."
                    )
            else:
                prior_rows = JournalLine.unscoped.filter(
                    organization_id=locked.organization_id,
                    entry__status__in=(JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED),
                    entry__entry_date__gte=fiscal_year_start,
                    entry__entry_date__lte=locked.closing_period_end,
                    entry__is_year_end_closing=False,
                    account__category__in=(Account.Category.INCOME, Account.Category.EXPENSE),
                ).values("account_id").annotate(
                    debit=Sum("base_debit_amount"),
                    credit=Sum("base_credit_amount"),
                )
                expected_closures = {
                    row["account_id"]: (row["debit"] or Decimal("0")) - (row["credit"] or Decimal("0"))
                    for row in prior_rows
                    if (row["debit"] or Decimal("0")) != (row["credit"] or Decimal("0"))
                }
                actual_closures = {}
                for line in profit_loss_lines:
                    actual_closures[line.account_id] = (
                        actual_closures.get(line.account_id, Decimal("0"))
                        + line.base_credit_amount - line.base_debit_amount
                    )
                expected_closures = {
                    account_id: amount for account_id, amount in expected_closures.items()
                    if amount
                }
                actual_closures = {
                    account_id: amount for account_id, amount in actual_closures.items()
                    if amount
                }
                if actual_closures != expected_closures:
                    raise ValidationError(
                        "The closing journal must clear every income and expense account "
                        "to its exact balance for the fiscal year being closed."
                    )

                retained_transfer = sum(
                    (line.base_debit_amount - line.base_credit_amount for line in retained_lines),
                    Decimal("0"),
                )
                required_transfer = sum(
                    (line.base_credit_amount - line.base_debit_amount for line in profit_loss_lines),
                    Decimal("0"),
                )
                if retained_transfer != required_transfer:
                    raise ValidationError(
                        "The retained-earnings transfer must equal the fiscal year's actual net profit or loss."
                    )

        debit_total = sum((line.debit_amount for line in lines), Decimal("0"))
        credit_total = sum((line.credit_amount for line in lines), Decimal("0"))
        if debit_total != credit_total:
            raise ValidationError(
                f"Journal is not balanced: debits {debit_total} do not equal credits {credit_total}."
            )

        base_debit_total = sum((line.base_debit_amount for line in lines), Decimal("0"))
        base_credit_total = sum((line.base_credit_amount for line in lines), Decimal("0"))
        if base_debit_total != base_credit_total:
            raise ValidationError(
                "Base-currency debits and credits do not balance after conversion. "
                "Adjust the line amounts or exchange rate before posting."
            )

        locked.status = JournalEntry.Status.POSTED
        locked.posted_at = timezone.now()
        locked.posted_by = posted_by
        locked.save()
        return locked


@transaction.atomic
def reverse_journal_entry(entry, *, entry_date=None, description=None, posted_by=None):
    """Create and post an equal-and-opposite journal, then mark the source reversed."""
    with organization_context(entry.organization_id):
        original = JournalEntry.unscoped.select_for_update().get(pk=entry.pk)
        if original.status != JournalEntry.Status.POSTED:
            raise ValidationError("Only posted journal entries can be reversed.")
        if JournalEntry.unscoped.filter(reversal_of=original).exists():
            raise ValidationError("This journal entry has already been reversed.")

        original_lines = list(
            JournalLine.unscoped.select_related("account").filter(entry=original)
        )
        reversal_lines = [
            {
                "account": line.account,
                "description": line.description,
                "cash_flow_category": line.cash_flow_category,
                "debit": line.credit_amount,
                "credit": line.debit_amount,
            }
            for line in original_lines
        ]
        reversal = create_journal_entry(
            original.organization,
            entry_date=entry_date or timezone.localdate(),
            description=description or f"Reversal of {original.reference or original.pk}",
            reference=f"REV-{original.reference}"[:50] if original.reference else "",
            currency=original.currency,
            exchange_rate=original.exchange_rate,
            lines=reversal_lines,
            reversal_of=original,
            is_year_end_closing=original.is_year_end_closing,
            closing_period_end=original.closing_period_end,
        )
        post_journal_entry(reversal, posted_by=posted_by)
        JournalEntry.unscoped.filter(pk=original.pk).update(
            status=JournalEntry.Status.REVERSED
        )
        return JournalEntry.unscoped.get(pk=reversal.pk)
