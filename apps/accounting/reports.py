"""Financial statements built only from posted journals, in the company's base currency.

Reversed entries count (the reversal entry cancels them), drafts never do.
Sums are exact; each presented figure is rounded to 2 decimals (half up).
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from .models import Account, JournalEntry, JournalLine

TWO_PLACES = Decimal("0.01")
COUNTED = (JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED)


def _money(value):
    return (value or Decimal("0")).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _totals(*, start_date=None, end_date):
    """Return {account_id: (debit, credit)} for counted lines up to `end_date`."""
    lines = JournalLine.objects.filter(
        entry__status__in=COUNTED, entry__entry_date__lte=end_date
    )
    if start_date is not None:
        lines = lines.filter(entry__entry_date__gte=start_date)
    rows = lines.values("account_id").annotate(
        debit=Sum("base_debit_amount"), credit=Sum("base_credit_amount")
    )
    return {r["account_id"]: (r["debit"] or Decimal("0"), r["credit"] or Decimal("0")) for r in rows}


def trial_balance(*, as_of):
    """Every account with a balance at `as_of`, on its debit or credit side."""
    totals = _totals(end_date=as_of)
    accounts = Account.objects.filter(id__in=totals.keys()).order_by("code")
    rows = []
    total_debit = total_credit = Decimal("0")
    for account in accounts:
        debit, credit = totals[account.id]
        balance = debit - credit
        if balance == 0:
            continue
        side_debit = balance if balance > 0 else Decimal("0")
        side_credit = -balance if balance < 0 else Decimal("0")
        total_debit += side_debit
        total_credit += side_credit
        rows.append(
            {
                "code": account.code,
                "name": account.name,
                "category": account.category,
                "debit": _money(side_debit),
                "credit": _money(side_credit),
            }
        )
    return {
        "as_of": as_of,
        "rows": rows,
        "total_debit": _money(total_debit),
        "total_credit": _money(total_credit),
        "balanced": total_debit == total_credit,
    }


def income_statement(*, start_date, end_date):
    """Income, cost of sales and expenses for the date range, ending in net profit."""
    totals = _totals(start_date=start_date, end_date=end_date)
    accounts = Account.objects.filter(
        id__in=totals.keys(),
        category__in=[Account.Category.INCOME, Account.Category.EXPENSE],
    ).order_by("code")

    income, cost_of_sales, expenses = [], [], []
    sums = {"income": Decimal("0"), "cost": Decimal("0"), "expenses": Decimal("0")}
    for account in accounts:
        debit, credit = totals[account.id]
        if account.category == Account.Category.INCOME:
            amount = credit - debit
            target, key = income, "income"
        elif account.account_type == Account.AccountType.COST_OF_SALES:
            amount = debit - credit
            target, key = cost_of_sales, "cost"
        else:
            amount = debit - credit
            target, key = expenses, "expenses"
        if amount == 0:
            continue
        sums[key] += amount
        target.append({"code": account.code, "name": account.name, "amount": _money(amount)})

    gross_profit = sums["income"] - sums["cost"]
    net_profit = gross_profit - sums["expenses"]
    return {
        "start_date": start_date,
        "end_date": end_date,
        "income": income,
        "total_income": _money(sums["income"]),
        "cost_of_sales": cost_of_sales,
        "total_cost_of_sales": _money(sums["cost"]),
        "gross_profit": _money(gross_profit),
        "expenses": expenses,
        "total_expenses": _money(sums["expenses"]),
        "net_profit": _money(net_profit),
    }


def _fiscal_year_start(as_of, month):
    from datetime import date

    year = as_of.year if as_of.month >= month else as_of.year - 1
    return date(year, month, 1)


def retained_earnings(*, as_of):
    """Reconcile the retained-earnings ledger with unclosed historical/current profit.

    Prior-period profit is included only while it remains in income/expense
    accounts. A proper closing journal moves it into the retained-earnings
    account and therefore removes it from the unclosed-profit calculation.
    """
    from datetime import date, timedelta

    from apps.core.tenancy import get_current_org_id
    from apps.organizations.models import Organization

    org = Organization.objects.get(pk=get_current_org_id())
    year_start = _fiscal_year_start(as_of, org.fiscal_year_start_month)
    totals = _totals(end_date=as_of)
    retained_account_ids = Account.objects.filter(
        account_type=Account.AccountType.RETAINED_EARNINGS
    ).values_list("id", flat=True)
    ledger_balance = Decimal("0")
    for account_id in retained_account_ids:
        debit, credit = totals.get(account_id, (Decimal("0"), Decimal("0")))
        ledger_balance += credit - debit

    prior_profit = Decimal("0")
    if year_start > date.min:
        prior_profit = income_statement(
            start_date=date.min, end_date=year_start - timedelta(days=1)
        )["net_profit"]
    current_profit = income_statement(
        start_date=year_start, end_date=as_of
    )["net_profit"]
    return {
        "as_of": as_of,
        "fiscal_year_start": year_start,
        "retained_earnings_ledger": _money(ledger_balance),
        "prior_unclosed_profit": _money(prior_profit),
        "current_year_profit": _money(current_profit),
        "total_retained_earnings": _money(
            ledger_balance + prior_profit + current_profit
        ),
    }


def balance_sheet(*, as_of):
    """Return assets, liabilities and equity, including unclosed earnings."""
    totals = _totals(end_date=as_of)
    accounts = Account.objects.filter(id__in=totals.keys()).order_by("code")
    assets, liabilities, equity = [], [], []
    asset_total = liability_total = equity_ledger_total = Decimal("0")

    for account in accounts:
        debit, credit = totals[account.id]
        if account.category == Account.Category.ASSET:
            amount = debit - credit
            section = assets
            asset_total += amount
        elif account.category == Account.Category.LIABILITY:
            amount = credit - debit
            section = liabilities
            liability_total += amount
        elif account.category == Account.Category.EQUITY:
            amount = credit - debit
            section = equity
            equity_ledger_total += amount
        else:
            continue
        if amount:
            section.append({
                "code": account.code,
                "name": account.name,
                "account_type": account.account_type,
                "amount": _money(amount),
            })

    earnings = retained_earnings(as_of=as_of)
    # The retained-earnings ledger row is already included above. Add only
    # unclosed prior/current profit as additional equity components.
    unclosed = earnings["prior_unclosed_profit"] + earnings["current_year_profit"]
    if earnings["prior_unclosed_profit"]:
        equity.append({
            "code": None,
            "name": "Prior unclosed earnings",
            "account_type": "prior_unclosed_earnings",
            "amount": earnings["prior_unclosed_profit"],
        })
    if earnings["current_year_profit"]:
        equity.append({
            "code": None,
            "name": "Current year earnings",
            "account_type": "current_year_earnings",
            "amount": earnings["current_year_profit"],
        })
    equity_total = equity_ledger_total + unclosed
    return {
        "as_of": as_of,
        "assets": assets,
        "total_assets": _money(asset_total),
        "liabilities": liabilities,
        "total_liabilities": _money(liability_total),
        "equity": equity,
        "total_equity": _money(equity_total),
        "total_liabilities_and_equity": _money(liability_total + equity_total),
        "balanced": _money(asset_total) == _money(liability_total + equity_total),
        "difference": _money(asset_total - liability_total - equity_total),
        "retained_earnings": earnings,
    }


def comparative_income_statement(*, start_date, end_date, compare_start, compare_end):
    current = income_statement(start_date=start_date, end_date=end_date)
    comparison = income_statement(start_date=compare_start, end_date=compare_end)
    metrics = (
        "total_income", "total_cost_of_sales", "gross_profit",
        "total_expenses", "net_profit",
    )
    variance = {
        key: _money(current[key] - comparison[key]) for key in metrics
    }
    return {
        "current": current,
        "comparison": comparison,
        "variance": variance,
    }


def comparative_balance_sheet(*, as_of, compare_as_of):
    current = balance_sheet(as_of=as_of)
    comparison = balance_sheet(as_of=compare_as_of)
    metrics = ("total_assets", "total_liabilities", "total_equity")
    variance = {
        key: _money(current[key] - comparison[key]) for key in metrics
    }
    return {
        "current": current,
        "comparison": comparison,
        "variance": variance,
    }


def cash_flow_statement(*, start_date, end_date):
    """Classify posted cash/bank movements using their journal counterpart accounts.

    Account-type mapping is deliberately explicit. Transactions whose
    counterpart cannot be classified are returned as unclassified rather
    than silently being reported as operating cash flow.
    """
    from collections import defaultdict
    from datetime import timedelta

    from .models import JournalEntry

    operating_types = {
        Account.AccountType.ACCOUNTS_RECEIVABLE,
        Account.AccountType.INVENTORY,
        Account.AccountType.PREPAID_EXPENSE,
        Account.AccountType.ACCOUNTS_PAYABLE,
        Account.AccountType.VAT_PAYABLE,
        Account.AccountType.PAYE_PAYABLE,
        Account.AccountType.WITHHOLDING_TAX_PAYABLE,
        Account.AccountType.SALES_REVENUE,
        Account.AccountType.SERVICE_REVENUE,
        Account.AccountType.OTHER_INCOME,
        Account.AccountType.COST_OF_SALES,
        Account.AccountType.SALARIES,
        Account.AccountType.RENT,
        Account.AccountType.UTILITIES,
        Account.AccountType.TRANSPORT,
        Account.AccountType.OFFICE_SUPPLIES,
        Account.AccountType.BANK_CHARGES,
        Account.AccountType.DEPRECIATION_EXPENSE,
        Account.AccountType.REPAIRS,
        Account.AccountType.TAX_EXPENSE,
        Account.AccountType.OTHER_EXPENSE,
    }
    investing_types = {
        Account.AccountType.FIXED_ASSET,
        Account.AccountType.ACCUMULATED_DEPRECIATION,
    }
    financing_types = {
        Account.AccountType.LOAN_PAYABLE,
        Account.AccountType.OWNER_CAPITAL,
        Account.AccountType.RETAINED_EARNINGS,
        Account.AccountType.DRAWINGS,
    }
    cash_types = {Account.AccountType.CASH, Account.AccountType.BANK}
    categories = {
        "operating": defaultdict(Decimal),
        "investing": defaultdict(Decimal),
        "financing": defaultdict(Decimal),
        "unclassified": defaultdict(Decimal),
    }

    entries = JournalEntry.objects.filter(
        status__in=COUNTED,
        entry_date__gte=start_date,
        entry_date__lte=end_date,
    ).prefetch_related("lines__account")
    for entry in entries:
        lines = list(entry.lines.all())
        cash_delta = sum(
            (line.base_debit_amount - line.base_credit_amount)
            for line in lines if line.account.account_type in cash_types
        )
        if not cash_delta:
            continue
        counterpart_lines = [
            line for line in lines if line.account.account_type not in cash_types
        ]
        total_weight = sum(
            (line.base_debit_amount + line.base_credit_amount)
            for line in counterpart_lines
        )
        if not counterpart_lines or not total_weight:
            categories["unclassified"]["Unclassified cash movement"] += cash_delta
            continue
        allocated = Decimal("0")
        for index, line in enumerate(counterpart_lines):
            weight = line.base_debit_amount + line.base_credit_amount
            amount = (
                cash_delta - allocated if index == len(counterpart_lines) - 1
                else cash_delta * weight / total_weight
            )
            allocated += amount
            account_type = line.account.account_type
            if account_type in operating_types:
                group = "operating"
            elif account_type in investing_types:
                group = "investing"
            elif account_type in financing_types:
                group = "financing"
            else:
                group = "unclassified"
            categories[group][line.account.name] += amount

    opening_totals = _totals(end_date=start_date - timedelta(days=1))
    closing_totals = _totals(end_date=end_date)
    cash_accounts = Account.objects.filter(account_type__in=cash_types)
    opening_cash = closing_cash = Decimal("0")
    for account in cash_accounts:
        opening_debit, opening_credit = opening_totals.get(
            account.id, (Decimal("0"), Decimal("0"))
        )
        closing_debit, closing_credit = closing_totals.get(
            account.id, (Decimal("0"), Decimal("0"))
        )
        opening_cash += opening_debit - opening_credit
        closing_cash += closing_debit - closing_credit

    result = {}
    for group, rows in categories.items():
        total = sum(rows.values(), Decimal("0"))
        result[group] = {
            "total": _money(total),
            "accounts": [
                {"name": name, "net_cash_flow": _money(amount)}
                for name, amount in sorted(rows.items()) if amount
            ],
        }
    classified_total = sum(
        (result[group]["total"] for group in result), Decimal("0")
    )
    net_change = closing_cash - opening_cash
    return {
        "start_date": start_date,
        "end_date": end_date,
        "opening_cash_and_bank": _money(opening_cash),
        "operating": result["operating"],
        "investing": result["investing"],
        "financing": result["financing"],
        "unclassified": result["unclassified"],
        "net_change_in_cash": _money(net_change),
        "closing_cash_and_bank": _money(closing_cash),
        "reconciliation_difference": _money(net_change - classified_total),
        "classification_note": (
            "Cash flows are classified from journal counterpart account types. "
            "Review unclassified items; transfers between cash/bank accounts net to zero."
        ),
    }
