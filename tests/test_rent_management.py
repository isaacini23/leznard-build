from datetime import date
from decimal import Decimal

import pytest

from apps.accounting.models import Account
from apps.accounting.services import create_default_chart_of_accounts
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization
from tests.test_properties import call, make_contact, make_property, register, staff_client


pytestmark = pytest.mark.django_db


def active_tenancy(client, org_id, *, start="2027-01-31", frequency="monthly"):
    prop = make_property(client, org_id, "Rent Court").json()
    unit = call(
        client, "post", f"/api/v1/properties/{prop['id']}/units/", org_id,
        {"label": "A1", "unit_type": "flat"},
    ).json()
    tenant = make_contact(client, org_id, "Ada Tenant", "tenant")
    tenancy = call(
        client, "post", "/api/v1/tenancies/", org_id,
        {
            "unit": unit["id"], "tenant": tenant["id"], "start_date": start,
            "rent_amount": "120000", "rent_frequency": frequency,
        },
    )
    assert tenancy.status_code == 201, tenancy.content
    activated = call(
        client, "post", f"/api/v1/tenancies/{tenancy.json()['id']}/activate/", org_id
    )
    assert activated.status_code == 200, activated.content
    return prop, unit, tenant, activated.json()


def configure_schedule(client, org_id, tenancy, *, next_date="2027-01-31", due_days=5):
    organization = Organization.objects.get(pk=org_id)
    with organization_context(organization.pk):
        create_default_chart_of_accounts(organization)
        revenue = Account.objects.get(account_type=Account.AccountType.SALES_REVENUE)
    response = call(
        client, "post", f"/api/v1/tenancies/{tenancy['id']}/rent-schedule/", org_id,
        {
            "revenue_account": str(revenue.pk),
            "next_invoice_date": next_date,
            "due_days": due_days,
        },
    )
    assert response.status_code == 201, response.content
    return response.json()


def test_rent_schedule_generates_linked_sales_invoices_and_preserves_billing_day():
    client, org_id = register()
    _, _, _, tenancy = active_tenancy(client, org_id)
    schedule = configure_schedule(client, org_id, tenancy)

    first = call(
        client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert first.status_code == 201, first.content
    first_data = first.json()
    assert first_data["period_start"] == "2027-01-31"
    assert first_data["period_end"] == "2027-02-27"
    assert first_data["invoice_status"] == "draft"
    assert first_data["total"] == "120000.0000"

    current_schedule = call(
        client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-schedule/", org_id
    ).json()
    assert current_schedule["next_invoice_date"] == "2027-02-28"
    assert current_schedule["billing_day"] == 31

    second = call(
        client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert second.status_code == 201, second.content
    assert second.json()["period_start"] == "2027-02-28"
    assert second.json()["period_end"] == "2027-03-30"

    charges = call(
        client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-charges/", org_id
    ).json()
    assert len(charges) == 2
    assert charges[0]["invoice_id"] == second.json()["invoice_id"]


def test_rent_invoice_posts_to_ledger_and_accepts_tenant_receipts():
    client, org_id = register()
    _, _, tenant, tenancy = active_tenancy(client, org_id)
    schedule = configure_schedule(client, org_id, tenancy)
    generated = call(
        client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert generated.status_code == 201, generated.content
    invoice_id = generated.json()["invoice_id"]

    posted_invoice = call(
        client, "post", f"/api/v1/sales-invoices/{invoice_id}/post/", org_id
    )
    assert posted_invoice.status_code == 200, posted_invoice.content
    assert posted_invoice.json()["status"] == "posted"

    organization = Organization.objects.get(pk=org_id)
    with organization_context(organization.pk):
        bank = Account.objects.get(account_type=Account.AccountType.BANK)
    receipt = call(
        client, "post", "/api/v1/payments/", org_id,
        {
            "payment_type": "customer_receipt",
            "payment_date": "2027-02-01",
            "contact": tenant["id"],
            "account": str(bank.pk),
            "amount": "40000",
            "currency": "NGN",
            "allocations": [{"sales_invoice": invoice_id, "amount": "40000"}],
        },
    )
    assert receipt.status_code == 201, receipt.content
    posted_receipt = call(
        client, "post", f"/api/v1/payments/{receipt.json()['id']}/post/", org_id
    )
    assert posted_receipt.status_code == 200, posted_receipt.content

    charge = call(
        client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-charges/", org_id
    ).json()[0]
    assert charge["amount_paid"] == "40000.0000"
    assert charge["amount_due"] == "80000.0000"
    assert charge["status"] == "partially_paid"


def test_rent_schedule_requires_active_tenancy_and_income_account():
    client, org_id = register()
    prop = make_property(client, org_id).json()
    unit = call(
        client, "post", f"/api/v1/properties/{prop['id']}/units/", org_id,
        {"label": "A1", "unit_type": "flat"},
    ).json()
    tenant = make_contact(client, org_id, "Ada Tenant", "tenant")
    draft = call(
        client, "post", "/api/v1/tenancies/", org_id,
        {
            "unit": unit["id"], "tenant": tenant["id"], "start_date": "2027-01-01",
            "rent_amount": "1000", "rent_frequency": "monthly",
        },
    ).json()
    organization = Organization.objects.get(pk=org_id)
    with organization_context(organization.pk):
        create_default_chart_of_accounts(organization)
        expense = Account.objects.get(account_type=Account.AccountType.OTHER_EXPENSE)
    rejected = call(
        client, "post", f"/api/v1/tenancies/{draft['id']}/rent-schedule/", org_id,
        {"revenue_account": str(expense.pk), "next_invoice_date": "2027-01-01"},
    )
    assert rejected.status_code == 400

    activated = call(client, "post", f"/api/v1/tenancies/{draft['id']}/activate/", org_id)
    assert activated.status_code == 200
    schedule = configure_schedule(client, org_id, activated.json(), next_date="2027-01-01")
    generated = call(
        client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert generated.status_code == 201
    ended = call(
        client, "post", f"/api/v1/tenancies/{draft['id']}/end/", org_id,
        {"end_date": "2027-01-31", "reason": "Moved out"},
    )
    assert ended.status_code == 200
    inactive = call(
        client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert inactive.status_code == 400


def test_rent_schedule_and_charges_are_tenant_isolated():
    owner, org_id = register()
    _, _, _, tenancy = active_tenancy(owner, org_id)
    schedule = configure_schedule(owner, org_id, tenancy)
    generated = call(
        owner, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert generated.status_code == 201

    other, other_org = register("other@example.com", "Other Company")
    assert call(
        other, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-schedule/", other_org
    ).status_code == 404
    assert call(
        other, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", other_org
    ).status_code == 404



def test_property_officer_can_record_only_tenant_rent_receipts():
    owner, org_id = register()
    _, _, tenant, tenancy = active_tenancy(owner, org_id)
    schedule = configure_schedule(owner, org_id, tenancy)
    generated = call(
        owner, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    invoice_id = generated.json()["invoice_id"]
    posted = call(owner, "post", f"/api/v1/sales-invoices/{invoice_id}/post/", org_id)
    assert posted.status_code == 200, posted.content

    organization = Organization.objects.get(pk=org_id)
    with organization_context(organization.pk):
        bank = Account.objects.get(account_type=Account.AccountType.BANK)
    officer = staff_client(org_id, "Property officer", "property-officer@example.com")
    receipt = call(
        officer, "post", "/api/v1/payments/", org_id,
        {
            "payment_type": "customer_receipt",
            "payment_date": "2027-02-01",
            "contact": tenant["id"],
            "account": str(bank.pk),
            "amount": "30000",
            "currency": "NGN",
            "allocations": [{"sales_invoice": invoice_id, "amount": "30000"}],
        },
    )
    assert receipt.status_code == 201, receipt.content
    assert call(officer, "get", "/api/v1/payments/", org_id).status_code == 200
    assert call(
        officer, "get", f"/api/v1/payments/{receipt.json()['id']}/", org_id
    ).status_code == 200
    assert call(
        officer, "post", f"/api/v1/payments/{receipt.json()['id']}/post/", org_id
    ).status_code == 403

    non_rent = call(
        officer, "post", "/api/v1/payments/", org_id,
        {
            "payment_type": "customer_receipt",
            "payment_date": "2027-02-01",
            "contact": tenant["id"],
            "account": str(bank.pk),
            "amount": "1000",
        },
    )
    assert non_rent.status_code == 400
