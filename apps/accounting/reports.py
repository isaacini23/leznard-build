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


def _totals(*, start_date=None, end_date, exclude_year_end_closing=False):
    """Return {account_id: (debit, credit)} for counted lines up to `end_date`."""
    lines = JournalLine.objects.filter(
        entry__status__in=COUNTED, entry__entry_date__lte=end_date
    )
    if start_date is not None:
        lines = lines.filter(entry__entry_date__gte=start_date)
    if exclude_year_end_closing:
        lines = lines.filter(entry__is_year_end_closing=False)
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
    totals = _totals(
        start_date=start_date, end_date=end_date,
        exclude_year_end_closing=True,
    )
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

    # A closing journal can be posted at year-end or after the new fiscal year
    # begins. Track those transfers explicitly so the prior profit is removed
    # from "unclosed" earnings exactly once. Reversals retain the flag and net
    # the transfer back out.
    closing_lines = JournalLine.objects.filter(
        entry__status__in=COUNTED,
        entry__is_year_end_closing=True,
        entry__entry_date__lte=as_of,
        entry__closing_period_end__lte=as_of,
        account__account_type=Account.AccountType.RETAINED_EARNINGS,
    )

    def closing_transfer_for_period(queryset):
        amounts = queryset.aggregate(
            debit=Sum("base_debit_amount"),
            credit=Sum("base_credit_amount"),
        )
        return (
            (amounts["credit"] or Decimal("0"))
            - (amounts["debit"] or Decimal("0"))
        )

    prior_closing_transfer = closing_transfer_for_period(
        closing_lines.filter(entry__closing_period_end__lt=year_start)
    )
    current_closing_transfer = closing_transfer_for_period(
        closing_lines.filter(
            entry__closing_period_end__gte=year_start,
            entry__closing_period_end__lte=as_of,
        )
    )
    prior_profit -= prior_closing_transfer

    current_profit = income_statement(
        start_date=year_start, end_date=as_of
    )["net_profit"] - current_closing_transfer
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


def _variance(current, comparison, metrics):
    amounts = {
        key: _money(current[key] - comparison[key])
        for key in metrics
    }
    percentages = {}
    for key in metrics:
        prior = comparison[key]
        percentages[key] = (
            None if prior == 0
            else _money((current[key] - prior) / abs(prior) * Decimal("100"))
        )
    return amounts, percentages


def comparative_income_statement(*, start_date, end_date, compare_start, compare_end):
    current = income_statement(start_date=start_date, end_date=end_date)
    comparison = income_statement(start_date=compare_start, end_date=compare_end)
    metrics = (
        "total_income", "total_cost_of_sales", "gross_profit",
        "total_expenses", "net_profit",
    )
    variance, variance_percent = _variance(current, comparison, metrics)
    return {
        "current": current,
        "comparison": comparison,
        "variance": variance,
        "variance_percent": variance_percent,
    }


def comparative_balance_sheet(*, as_of, compare_as_of):
    current = balance_sheet(as_of=as_of)
    comparison = balance_sheet(as_of=compare_as_of)
    metrics = ("total_assets", "total_liabilities", "total_equity")
    variance, variance_percent = _variance(current, comparison, metrics)
    return {
        "current": current,
        "comparison": comparison,
        "variance": variance,
        "variance_percent": variance_percent,
    }


def _account_balances_by_type(*, as_of):
    totals = _totals(end_date=as_of)
    accounts = Account.objects.filter(id__in=totals.keys())
    balances = {}
    for account in accounts:
        debit, credit = totals[account.id]
        # Use debit-positive balances for assets and credit-positive for liabilities.
        if account.category == Account.Category.LIABILITY:
            amount = credit - debit
        else:
            amount = debit - credit
        balances[account.account_type] = balances.get(
            account.account_type, Decimal("0")
        ) + amount
    return balances


def cash_flow_statement(*, start_date, end_date):
    """Prepare an indirect-method cash flow statement in base currency.

    Operating cash flow starts with net profit, adds back non-cash expenses,
    and adjusts for movements in operating working-capital accounts. Investing
    and financing cash movements use explicit journal-line classifications
    where supplied, with conservative account-type fallbacks. Unknown flows
    remain visible as unclassified.
    """
    from collections import defaultdict
    from datetime import timedelta

    cash_types = {Account.AccountType.CASH, Account.AccountType.BANK}
    current_asset_types = {
        Account.AccountType.ACCOUNTS_RECEIVABLE,
        Account.AccountType.INVENTORY,
        Account.AccountType.PREPAID_EXPENSE,
    }
    current_liability_types = {
        Account.AccountType.ACCOUNTS_PAYABLE,
        Account.AccountType.VAT_PAYABLE,
        Account.AccountType.PAYE_PAYABLE,
        Account.AccountType.WITHHOLDING_TAX_PAYABLE,
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

    opening_date = start_date - timedelta(days=1)
    opening_balances = _account_balances_by_type(as_of=opening_date)
    closing_balances = _account_balances_by_type(as_of=end_date)
    period_totals = _totals(
        start_date=start_date, end_date=end_date,
        exclude_year_end_closing=True,
    )

    # Depreciation is the standard non-cash add-back. Other non-cash items
    # can be marked explicitly on their journal lines.
    depreciation_ids = Account.objects.filter(
        account_type=Account.AccountType.DEPRECIATION_EXPENSE
    ).values_list("id", flat=True)
    depreciation = sum(
        (
            period_totals.get(account_id, (Decimal("0"), Decimal("0")))[0]
            - period_totals.get(account_id, (Decimal("0"), Decimal("0")))[1]
            for account_id in depreciation_ids
        ),
        Decimal("0"),
    )
    explicit_non_cash = JournalLine.objects.filter(
        entry__status__in=COUNTED,
        entry__entry_date__gte=start_date,
        entry__entry_date__lte=end_date,
        cash_flow_category=JournalLine.CashFlowCategory.NON_CASH_ADJUSTMENT,
        entry__is_year_end_closing=False,
    ).exclude(account__account_type=Account.AccountType.DEPRECIATION_EXPENSE)
    other_non_cash = sum(
        (
            line.base_debit_amount - line.base_credit_amount
            for line in explicit_non_cash
        ),
        Decimal("0"),
    )
    non_cash_total = depreciation + other_non_cash

    working_capital_rows = []
    working_capital_adjustment = Decimal("0")
    for account_type in sorted(current_asset_types | current_liability_types):
        opening = opening_balances.get(account_type, Decimal("0"))
        closing = closing_balances.get(account_type, Decimal("0"))
        movement = closing - opening
        adjustment = -movement if account_type in current_asset_types else movement
        working_capital_adjustment += adjustment
        if movement:
            working_capital_rows.append({
                "account_type": account_type,
                "opening_balance": _money(opening),
                "closing_balance": _money(closing),
                "movement": _money(movement),
                "cash_flow_adjustment": _money(adjustment),
            })

    net_profit = income_statement(
        start_date=start_date, end_date=end_date
    )["net_profit"]
    operating_total = net_profit + non_cash_total + working_capital_adjustment
    operating_adjustments = defaultdict(Decimal)
    operating_account_types = current_asset_types | current_liability_types
    known_profit_loss_types = {
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
    operating = {}

    # Investing/financing movements are classified at counterpart-line level.
    # This supports a single journal containing several different purposes.
    from .models import JournalEntry
    categories = {
        "investing": defaultdict(Decimal),
        "financing": defaultdict(Decimal),
        "unclassified": defaultdict(Decimal),
    }
    entries = JournalEntry.objects.filter(
        status__in=COUNTED,
        entry_date__gte=start_date,
        entry_date__lte=end_date,
        is_year_end_closing=False,
    ).prefetch_related("lines__account")
    for entry in entries:
        lines = list(entry.lines.all())
        cash_delta = sum(
            (
                line.base_debit_amount - line.base_credit_amount
                for line in lines if line.account.account_type in cash_types
            ),
            Decimal("0"),
        )
        if not cash_delta:
            continue
        counterpart_lines = [
            line for line in lines if line.account.account_type not in cash_types
        ]
        if not counterpart_lines:
            categories["unclassified"]["Unclassified cash movement"] += cash_delta
            continue

        for line in counterpart_lines:
            # On a balanced journal, each counterpart line's signed amount
            # (credit minus debit) is its exact contribution to net cash change.
            # This handles mixed inflows/outflows without allocating a net total
            # proportionally across unrelated lines.
            amount = line.base_credit_amount - line.base_debit_amount
            if not amount:
                continue

            explicit = line.cash_flow_category
            account_type = line.account.account_type
            if explicit == JournalLine.CashFlowCategory.INVESTING:
                group = "investing"
            elif explicit == JournalLine.CashFlowCategory.FINANCING:
                group = "financing"
            elif explicit == JournalLine.CashFlowCategory.OPERATING:
                if (
                    account_type not in operating_account_types
                    and account_type not in known_profit_loss_types
                ):
                    operating_adjustments[line.account.name] += amount
                continue
            elif explicit == JournalLine.CashFlowCategory.NON_CASH_ADJUSTMENT:
                # Non-cash adjustments belong in the add-back section, not
                # investing/financing cash flows.
                continue
            elif account_type in investing_types:
                group = "investing"
            elif account_type in financing_types:
                group = "financing"
            elif account_type in operating_account_types or account_type in known_profit_loss_types:
                continue
            else:
                group = "unclassified"
            categories[group][line.account.name] += amount

    operating_adjustment_total = sum(
        operating_adjustments.values(), Decimal("0")
    )
    operating_total += operating_adjustment_total
    operating = {
        "net_profit": _money(net_profit),
        "non_cash_adjustments": {
            "total": _money(non_cash_total),
            "depreciation": _money(depreciation),
            "other": _money(other_non_cash),
        },
        "working_capital_changes": working_capital_rows,
        "working_capital_adjustment": _money(working_capital_adjustment),
        "other_operating_adjustments": [
            {"name": name, "amount": _money(amount)}
            for name, amount in sorted(operating_adjustments.items()) if amount
        ],
        "other_operating_adjustment_total": _money(operating_adjustment_total),
        "total": _money(operating_total),
    }

    def category_payload(rows):
        total = sum(rows.values(), Decimal("0"))
        return {
            "total": _money(total),
            "accounts": [
                {"name": name, "net_cash_flow": _money(amount)}
                for name, amount in sorted(rows.items()) if amount
            ],
        }

    opening_totals = _totals(end_date=opening_date)
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

    investing = category_payload(categories["investing"])
    financing = category_payload(categories["financing"])
    unclassified = category_payload(categories["unclassified"])
    net_change = closing_cash - opening_cash
    classified_total = (
        operating_total
        + sum(categories["investing"].values(), Decimal("0"))
        + sum(categories["financing"].values(), Decimal("0"))
        + sum(categories["unclassified"].values(), Decimal("0"))
    )
    return {
        "start_date": start_date,
        "end_date": end_date,
        "method": "indirect",
        "opening_cash_and_bank": _money(opening_cash),
        "operating": operating,
        "investing": investing,
        "financing": financing,
        "unclassified": unclassified,
        "net_change_in_cash": _money(net_change),
        "closing_cash_and_bank": _money(closing_cash),
        "reconciliation_difference": _money(net_change - classified_total),
        "classification_note": (
            "Operating cash flow uses the indirect method. Mark journal lines "
            "as investing or financing when account type alone is ambiguous. "
            "Unclassified cash movements remain separate; transfers between "
            "cash/bank accounts net to zero."
        ),
    }


def comparative_cash_flow_statement(
    *, start_date, end_date, compare_start, compare_end
):
    current = cash_flow_statement(start_date=start_date, end_date=end_date)
    comparison = cash_flow_statement(
        start_date=compare_start, end_date=compare_end
    )
    metrics = (
        "opening_cash_and_bank", "operating", "investing", "financing",
        "unclassified", "net_change_in_cash", "closing_cash_and_bank",
    )
    current_values = {
        "opening_cash_and_bank": current["opening_cash_and_bank"],
        "operating": current["operating"]["total"],
        "investing": current["investing"]["total"],
        "financing": current["financing"]["total"],
        "unclassified": current["unclassified"]["total"],
        "net_change_in_cash": current["net_change_in_cash"],
        "closing_cash_and_bank": current["closing_cash_and_bank"],
    }
    comparison_values = {
        "opening_cash_and_bank": comparison["opening_cash_and_bank"],
        "operating": comparison["operating"]["total"],
        "investing": comparison["investing"]["total"],
        "financing": comparison["financing"]["total"],
        "unclassified": comparison["unclassified"]["total"],
        "net_change_in_cash": comparison["net_change_in_cash"],
        "closing_cash_and_bank": comparison["closing_cash_and_bank"],
    }
    variance, variance_percent = _variance(
        current_values, comparison_values, metrics
    )
    return {
        "current": current,
        "comparison": comparison,
        "variance": variance,
        "variance_percent": variance_percent,
    }
