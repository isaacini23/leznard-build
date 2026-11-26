from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from apps.accounting.models import Account
from apps.accounting.services import create_journal_entry, post_journal_entry
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db
PASSWORD = "Str0ng-pass-123"


def register(email, company):
    client = Client()
    response = client.post(
        "/api/v1/register/",
        {"email": email, "password": PASSWORD, "full_name": "Owner", "company_name": company},
        content_type="application/json",
    )
    assert response.status_code == 201
    return client, response.json()["organization"]["id"]


def test_chart_journal_ledger_and_balances_api():
    client, org_id = register("ledger-owner@example.com", "Ledger Ltd")
    headers = {"HTTP_X_ORGANIZATION_ID": org_id}

    accounts = client.get("/api/v1/accounts/", **headers)
    assert accounts.status_code == 200
    bank = next(item for item in accounts.json() if item["code"] == "1010")
    capital = next(item for item in accounts.json() if item["code"] == "3000")

    created = client.post(
        "/api/v1/accounts/",
        {"code": "1020", "name": "Savings Account", "category": "asset",
         "account_type": "bank", "normal_balance": "debit"},
        content_type="application/json", **headers,
    )
    assert created.status_code == 201
    assert created.json()["code"] == "1020"

    draft = client.post(
        "/api/v1/journals/",
        {"entry_date": "2026-06-01", "reference": "OPEN-001",
         "description": "Owner capital contribution",
         "lines": [
             {"account": bank["id"], "debit": "125000.00"},
             {"account": capital["id"], "credit": "125000.00"},
         ]},
        content_type="application/json", **headers,
    )
    assert draft.status_code == 201
    journal_id = draft.json()["id"]
    assert draft.json()["status"] == "draft"

    posted = client.post(f"/api/v1/journals/{journal_id}/post/", {}, content_type="application/json", **headers)
    assert posted.status_code == 200
    assert posted.json()["status"] == "posted"

    ledger = client.get(
        f"/api/v1/reports/general-ledger/?account={bank['id']}&start=2026-06-01&end=2026-06-30",
        **headers,
    )
    assert ledger.status_code == 200
    assert ledger.json()["opening_balance"] == "0"
    assert ledger.json()["closing_balance"] == "125000.0000000000"
    assert len(ledger.json()["entries"]) == 1

    balances = client.get("/api/v1/reports/account-balances/?as_of=2026-06-30", **headers)
    assert balances.status_code == 200
    bank_balance = next(item for item in balances.json()["accounts"] if item["id"] == bank["id"])
    assert Decimal(bank_balance["balance"]) == Decimal("125000")


def test_ledger_is_company_scoped():
    first, first_id = register("first-ledger@example.com", "First Ltd")
    second, second_id = register("second-ledger@example.com", "Second Ltd")
    first_headers = {"HTTP_X_ORGANIZATION_ID": first_id}
    second_headers = {"HTTP_X_ORGANIZATION_ID": second_id}
    with organization_context(Organization.objects.get(pk=first_id).pk):
        bank = Account.objects.get(code="1010")
        capital = Account.objects.get(code="3000")
        entry = create_journal_entry(
            Organization.objects.get(pk=first_id), entry_date=date(2026, 6, 1),
            description="Opening", lines=[
                {"account": bank, "debit": "500"},
                {"account": capital, "credit": "500"},
            ],
        )
        post_journal_entry(entry)
    url = f"/api/v1/reports/general-ledger/?account={bank.pk}&end=2026-06-30"
    assert first.get(url, **first_headers).status_code == 200
    assert second.get(url, **second_headers).status_code == 404
