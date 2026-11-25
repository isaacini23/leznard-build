from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from apps.access.models import Role, RoleAssignment
from apps.access.services import create_default_roles
from apps.accounting.models import Account
from apps.accounting.reports import income_statement, trial_balance
from apps.accounting.services import create_journal_entry, post_journal_entry, reverse_journal_entry
from apps.accounts.models import User
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership, Organization

pytestmark = pytest.mark.django_db
PASSWORD = "Str0ng-pass-123"


def book(org, on, debit_code, credit_code, amount, post=True, **kwargs):
    with organization_context(org.id):
        debit = Account.objects.get(code=debit_code)
        credit = Account.objects.get(code=credit_code)
        entry = create_journal_entry(
            org,
            entry_date=on,
            description="Test",
            lines=[{"account": debit, "debit": amount}, {"account": credit, "credit": amount}],
            **kwargs,
        )
    return post_journal_entry(entry) if post else entry


@pytest.fixture
def org():
    org = Organization.objects.create(name="Alpha Ltd")
    book(org, date(2026, 1, 5), "1010", "3000", "1000.00")   # capital paid into bank
    book(org, date(2026, 2, 10), "1010", "4100", "500.00")   # rent income
    book(org, date(2026, 2, 20), "6100", "1010", "200.00")   # rent expense
    book(org, date(2026, 3, 5), "5000", "1010", "50.00")    # cost of sales
    return org


def rows(report):
    return {r["code"]: (r["debit"], r["credit"]) for r in report["rows"]}


def test_trial_balance_balances_and_shows_each_account_on_its_side(org):
    with organization_context(org.id):
        tb = trial_balance(as_of=date(2026, 3, 31))
    assert tb["balanced"] is True
    assert tb["total_debit"] == tb["total_credit"] == Decimal("1500.00")
    assert rows(tb) == {
        "1010": (Decimal("1250.00"), Decimal("0.00")),
        "3000": (Decimal("0.00"), Decimal("1000.00")),
        "4100": (Decimal("0.00"), Decimal("500.00")),
        "5000": (Decimal("50.00"), Decimal("0.00")),
        "6100": (Decimal("200.00"), Decimal("0.00")),
    }


def test_trial_balance_respects_the_as_of_date(org):
    with organization_context(org.id):
        tb = trial_balance(as_of=date(2026, 2, 15))
    assert rows(tb) == {
        "1010": (Decimal("1500.00"), Decimal("0.00")),
        "3000": (Decimal("0.00"), Decimal("1000.00")),
        "4100": (Decimal("0.00"), Decimal("500.00")),
    }


def test_drafts_are_ignored(org):
    book(org, date(2026, 3, 6), "6100", "1010", "999.00", post=False)
    with organization_context(org.id):
        tb = trial_balance(as_of=date(2026, 3, 31))
    assert tb["total_debit"] == Decimal("1500.00")


def test_reversed_entry_nets_to_zero(org):
    entry = book(org, date(2026, 3, 8), "6100", "1010", "75.00")
    reverse_journal_entry(entry, entry_date=date(2026, 3, 9))
    with organization_context(org.id):
        tb = trial_balance(as_of=date(2026, 3, 31))
    assert rows(tb)["6100"] == (Decimal("200.00"), Decimal("0.00"))
    assert tb["balanced"] is True


def test_income_statement_for_a_range(org):
    with organization_context(org.id):
        full = income_statement(start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
        feb = income_statement(start_date=date(2026, 2, 1), end_date=date(2026, 2, 28))
    assert full["total_income"] == Decimal("500.00")
    assert full["total_cost_of_sales"] == Decimal("50.00")
    assert full["gross_profit"] == Decimal("450.00")
    assert full["total_expenses"] == Decimal("200.00")
    assert full["net_profit"] == Decimal("250.00")
    assert feb["net_profit"] == Decimal("300.00") and feb["total_cost_of_sales"] == Decimal("0.00")
    assert [r["code"] for r in full["income"]] == ["4100"]


def test_foreign_currency_entries_are_reported_in_base_currency():
    org = Organization.objects.create(name="Beta Ltd")
    book(org, date(2026, 4, 1), "1010", "4000", "100.00", currency="USD", exchange_rate="1500")
    with organization_context(org.id):
        report = income_statement(start_date=date(2026, 4, 1), end_date=date(2026, 4, 30))
    assert report["total_income"] == Decimal("150000.00")


def test_a_company_never_sees_another_companys_figures(org):
    other = Organization.objects.create(name="Beta Ltd")
    with organization_context(other.id):
        assert trial_balance(as_of=date(2026, 12, 31))["rows"] == []
        assert income_statement(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))["net_profit"] == Decimal("0.00")


def register(email, company):
    client = Client()
    response = client.post(
        "/api/v1/register/",
        {"email": email, "password": PASSWORD, "full_name": "Owner", "company_name": company},
        content_type="application/json",
    )
    return client, response.json()["organization"]["id"]


def test_api_returns_statements_and_validates_dates():
    client, org_id = register("owner@example.com", "Acme")
    book(Organization.objects.get(id=org_id), date(2026, 5, 1), "1010", "4000", "80.00")
    head = {"HTTP_X_ORGANIZATION_ID": org_id}

    tb = client.get("/api/v1/reports/trial-balance/?as_of=2026-05-31", **head)
    assert tb.status_code == 200
    body = tb.json()
    assert body["balanced"] is True and body["total_debit"] == "80.00"

    inc = client.get("/api/v1/reports/income-statement/?start=2026-05-01&end=2026-05-31", **head)
    assert inc.status_code == 200 and inc.json()["net_profit"] == "80.00"

    assert client.get("/api/v1/reports/trial-balance/", **head).status_code == 400
    assert client.get("/api/v1/reports/trial-balance/?as_of=yesterday", **head).status_code == 400
    bad = client.get("/api/v1/reports/income-statement/?start=2026-06-01&end=2026-05-01", **head)
    assert bad.status_code == 400


def test_reports_need_permission_and_stay_inside_the_company():
    owner, org_id = register("owner@example.com", "Acme")
    other, other_id = register("second@example.com", "Beta")
    org = Organization.objects.get(id=org_id)
    create_default_roles(org)
    employee = User.objects.create_user(email="emp@example.com", password=PASSWORD)
    auditor = User.objects.create_user(email="aud@example.com", password=PASSWORD)
    for user, role in ((employee, "Employee"), (auditor, "Auditor")):
        Membership.objects.create(user=user, organization=org)
        with organization_context(org.id):
            RoleAssignment.objects.create(user=user, role=Role.objects.get(name=role))
    url = "/api/v1/reports/trial-balance/?as_of=2026-12-31"
    head = {"HTTP_X_ORGANIZATION_ID": org_id}

    emp = Client(); emp.force_login(employee)
    aud = Client(); aud.force_login(auditor)
    assert emp.get(url, **head).status_code == 403
    assert aud.get(url, **head).status_code == 200
    assert other.get(url, **head).status_code == 403
