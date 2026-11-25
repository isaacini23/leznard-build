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
