from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounting.models import Account
from apps.accounting.reports import (
    balance_sheet,
    cash_flow_statement,
    comparative_balance_sheet,
    comparative_income_statement,
    retained_earnings,
)
from apps.accounting.services import create_journal_entry, post_journal_entry
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db
PASSWORD = "Str0ng-pass-123"


def book(org, on, debit_code, credit_code, amount):
    with organization_context(org.id):
        debit = Account.objects.get(code=debit_code)
        credit = Account.objects.get(code=credit_code)
        entry = create_journal_entry(
            org,
            entry_date=on,
            description="Financial statement test",
            lines=[
                {"account": debit, "debit": amount},
                {"account": credit, "credit": amount},
            ],
        )
    return post_journal_entry(entry)


@pytest.fixture
def org():
    company = Organization.objects.create(name="Statements Ltd")
    book(company, date(2026, 1, 5), "1010", "3000", "1000.00")
    book(company, date(2026, 2, 10), "1010", "4100", "500.00")
    book(company, date(2026, 2, 20), "6100", "1010", "200.00")
    book(company, date(2026, 3, 5), "1500", "1010", "300.00")
    return company


def test_balance_sheet_includes_unclosed_current_earnings_and_balances(org):
    with organization_context(org.id):
        report = balance_sheet(as_of=date(2026, 3, 31))

    assert report["total_assets"] == Decimal("1300.00")
    assert report["total_liabilities"] == Decimal("0.00")
    assert report["total_equity"] == Decimal("1300.00")
    assert report["total_liabilities_and_equity"] == Decimal("1300.00")
    assert report["balanced"] is True
    assert report["retained_earnings"]["current_year_profit"] == Decimal("300.00")


def test_retained_earnings_separates_ledger_and_unclosed_profit(org):
    with organization_context(org.id):
        report = retained_earnings(as_of=date(2026, 3, 31))

    assert report["retained_earnings_ledger"] == Decimal("0.00")
    assert report["prior_unclosed_profit"] == Decimal("0.00")
    assert report["current_year_profit"] == Decimal("300.00")
    assert report["total_retained_earnings"] == Decimal("300.00")


def test_cash_flow_statement_classifies_cash_movements_and_reconciles(org):
    with organization_context(org.id):
        report = cash_flow_statement(
            start_date=date(2026, 1, 1), end_date=date(2026, 3, 31)
        )

    assert report["opening_cash_and_bank"] == Decimal("0.00")
    assert report["operating"]["total"] == Decimal("300.00")
    assert report["investing"]["total"] == Decimal("-300.00")
    assert report["financing"]["total"] == Decimal("1000.00")
    assert report["unclassified"]["total"] == Decimal("0.00")
    assert report["net_change_in_cash"] == Decimal("1000.00")
    assert report["closing_cash_and_bank"] == Decimal("1000.00")
    assert report["reconciliation_difference"] == Decimal("0.00")


def test_comparative_income_statement_reports_period_variances(org):
    with organization_context(org.id):
        report = comparative_income_statement(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            compare_start=date(2025, 1, 1),
            compare_end=date(2025, 12, 31),
        )

    assert report["current"]["net_profit"] == Decimal("300.00")
    assert report["comparison"]["net_profit"] == Decimal("0.00")
    assert report["variance"]["net_profit"] == Decimal("300.00")


def test_comparative_balance_sheet_reports_as_of_variances(org):
    with organization_context(org.id):
        report = comparative_balance_sheet(
            as_of=date(2026, 3, 31), compare_as_of=date(2026, 1, 31)
        )

    assert report["current"]["total_assets"] == Decimal("1300.00")
    assert report["comparison"]["total_assets"] == Decimal("1000.00")
    assert report["variance"]["total_assets"] == Decimal("300.00")


def test_financial_statements_are_company_scoped(org):
    other = Organization.objects.create(name="Other Statements Ltd")
    with organization_context(other.id):
        balance = balance_sheet(as_of=date(2026, 3, 31))
        cash_flow = cash_flow_statement(
            start_date=date(2026, 1, 1), end_date=date(2026, 3, 31)
        )

    assert balance["total_assets"] == Decimal("0.00")
    assert balance["total_equity"] == Decimal("0.00")
    assert cash_flow["closing_cash_and_bank"] == Decimal("0.00")


def register(email, company):
    client = Client()
    response = client.post(
        "/api/v1/register/",
        {
            "email": email,
            "password": PASSWORD,
            "full_name": "Owner",
            "company_name": company,
        },
        content_type="application/json",
    )
    assert response.status_code == 201
    return client, response.json()["organization"]["id"]


def test_financial_statement_endpoints_and_date_validation():
    client, org_id = register("statement-owner@example.com", "API Statements Ltd")
    headers = {"HTTP_X_ORGANIZATION_ID": org_id}

    balance = client.get(
        "/api/v1/reports/balance-sheet/?as_of=2026-03-31", **headers
    )
    assert balance.status_code == 200
    assert balance.json()["balanced"] is True

    retained = client.get(
        "/api/v1/reports/retained-earnings/?as_of=2026-03-31", **headers
    )
    assert retained.status_code == 200

    cash = client.get(
        "/api/v1/reports/cash-flow/?start=2026-01-01&end=2026-03-31",
        **headers,
    )
    assert cash.status_code == 200

    comparative = client.get(
        "/api/v1/reports/comparative-income-statement/"
        "?start=2026-01-01&end=2026-03-31"
        "&compare_start=2025-01-01&compare_end=2025-12-31",
        **headers,
    )
    assert comparative.status_code == 200

    assert client.get(
        "/api/v1/reports/balance-sheet/", **headers
    ).status_code == 400
    assert client.get(
        "/api/v1/reports/cash-flow/?start=2026-04-01&end=2026-03-31",
        **headers,
    ).status_code == 400
