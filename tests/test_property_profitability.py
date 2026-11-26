import pytest

from apps.accounting.models import Account
from apps.accounting.services import create_default_chart_of_accounts
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization
from tests.test_properties import call, make_contact, make_property, register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule


pytestmark = pytest.mark.django_db


def create_property_rent_invoice(client, org_id):
    prop, _, tenant, tenancy = active_tenancy(client, org_id, start="2027-01-01")
    schedule = configure_schedule(client, org_id, tenancy, next_date="2027-01-01")
    generated = call(
        client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org_id
    )
    assert generated.status_code == 201, generated.content
    invoice_id = generated.json()["invoice_id"]
    posted = call(client, "post", f"/api/v1/sales-invoices/{invoice_id}/post/", org_id)
    assert posted.status_code == 200, posted.content
    return prop, tenant, invoice_id


def create_property_purchase_bill(client, org_id, prop, *, document_date="2027-01-10"):
    supplier = make_contact(client, org_id, "Property Supplies", "supplier")
    organization = Organization.objects.get(pk=org_id)
    with organization_context(organization.pk):
        create_default_chart_of_accounts(organization)
        expense = Account.objects.get(account_type=Account.AccountType.OTHER_EXPENSE)
    response = call(
        client, "post", "/api/v1/purchase-bills/", org_id,
        {
            "number": "PROP-EXP-001",
            "supplier": supplier["id"],
            "property": prop["id"],
            "document_date": document_date,
            "due_date": "2027-02-10",
            "currency": "NGN",
            "lines": [{
                "description": "Property repairs and supplies",
                "quantity": "1",
                "unit_price": "15000",
                "discount_amount": "0",
                "tax_amount": "0",
                "expense_account": str(expense.pk),
            }],
        },
    )
    assert response.status_code == 201, response.content
    return response.json(), supplier


def test_property_profitability_reports_accrual_and_cash_performance():
    client, org_id = register()
    prop, tenant, invoice_id = create_property_rent_invoice(client, org_id)
    bill, supplier = create_property_purchase_bill(client, org_id, prop)

    posted_bill = call(
        client, "post", f"/api/v1/purchase-bills/{bill['id']}/post/", org_id
    )
    assert posted_bill.status_code == 200, posted_bill.content

    organization = Organization.objects.get(pk=org_id)
    with organization_context(organization.pk):
        bank = Account.objects.get(account_type=Account.AccountType.BANK)
    receipt = call(
        client, "post", "/api/v1/payments/", org_id,
        {
            "payment_type": "customer_receipt",
            "payment_date": "2027-01-15",
            "contact": tenant["id"],
            "account": str(bank.pk),
            "amount": "50000",
            "currency": "NGN",
            "allocations": [{"sales_invoice": invoice_id, "amount": "50000"}],
        },
    )
    assert receipt.status_code == 201, receipt.content
    assert call(client, "post", f"/api/v1/payments/{receipt.json()['id']}/post/", org_id).status_code == 200

    supplier_payment = call(
        client, "post", "/api/v1/payments/", org_id,
        {
            "payment_type": "supplier_payment",
            "payment_date": "2027-01-20",
            "contact": supplier["id"],
            "account": str(bank.pk),
            "amount": "5000",
            "currency": "NGN",
            "allocations": [{"purchase_bill": bill["id"], "amount": "5000"}],
        },
    )
    assert supplier_payment.status_code == 201, supplier_payment.content
    assert call(
        client, "post", f"/api/v1/payments/{supplier_payment.json()['id']}/post/", org_id
    ).status_code == 200

    report = call(
        client, "get",
        f"/api/v1/properties/{prop['id']}/profitability/?start=2027-01-01&end=2027-01-31",
        org_id,
    )
    assert report.status_code == 200, report.content
    body = report.json()
    assert body["currency"] == "NGN"
    assert body["rent_billed"] == "120000.00"
    assert body["rent_collected"] == "50000.00"
    assert body["outstanding_rent_as_of_end_date"] == "70000.00"
    assert body["operating_expenses"] == "15000.00"
    assert body["operating_expenses_paid"] == "5000.00"
    assert body["net_operating_income"] == "105000.00"
    assert body["cash_net_operating_income"] == "45000.00"
    assert body["rent_invoice_count"] == 1 and body["expense_bill_count"] == 1


def test_draft_documents_are_excluded_and_date_range_is_respected():
    client, org_id = register()
    prop, _, _ = create_property_rent_invoice(client, org_id)
    create_property_purchase_bill(client, org_id, prop, document_date="2026-12-15")
    report = call(
        client, "get",
        f"/api/v1/properties/{prop['id']}/profitability/?start=2027-01-01&end=2027-01-31",
        org_id,
    )
    assert report.status_code == 200
    body = report.json()
    assert body["rent_billed"] == "120000.00"
    assert body["operating_expenses"] == "0.00"
    assert body["expense_bill_count"] == 0


def test_property_profitability_dates_are_required_and_ordered():
    client, org_id = register()
    prop = make_property(client, org_id).json()
    base = f"/api/v1/properties/{prop['id']}/profitability/"
    assert call(client, "get", base, org_id).status_code == 400
    assert call(client, "get", base + "?start=bad&end=2027-01-31", org_id).status_code == 400
    assert call(client, "get", base + "?start=2027-02-01&end=2027-01-31", org_id).status_code == 400


def test_property_profitability_permissions_and_organisation_isolation():
    owner, org_id = register()
    prop = make_property(owner, org_id).json()
    officer = staff_client(org_id, "Property officer", "property-officer@example.com")
    employee = staff_client(org_id, "Employee", "employee@example.com")
    url = f"/api/v1/properties/{prop['id']}/profitability/?start=2027-01-01&end=2027-01-31"
    assert call(officer, "get", url, org_id).status_code == 200
    assert call(employee, "get", url, org_id).status_code == 403

    other, other_org = register("other@example.com", "Other Company")
    assert call(other, "get", url, other_org).status_code == 404


def test_purchase_bill_property_must_belong_to_active_organisation():
    client, org_id = register()
    prop = make_property(client, org_id).json()
    other, other_org = register("other@example.com", "Other Company")
    foreign_property = make_property(other, other_org).json()
    bill, supplier = create_property_purchase_bill(client, org_id, prop)
    # A draft bill can be reassigned only to a property in its own organisation.
    rejected = call(
        client, "patch", f"/api/v1/purchase-bills/{bill['id']}/", org_id,
        {"property": foreign_property["id"]},
    )
    assert rejected.status_code == 400
