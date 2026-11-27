import pytest

from tests.test_owner_rent_actions import call
from tests.test_properties import make_contact, register, staff_client
from tests.test_property_profitability import create_property_purchase_bill, create_property_rent_invoice

pytestmark = pytest.mark.django_db


def statement(client, org, prop, start="2027-01-01", end="2027-01-31"):
    return call(client, "get", f"/api/v1/properties/{prop['id']}/owner-statement/?start={start}&end={end}", org)


def test_statement_splits_net_after_fee_and_expenses_between_owners():
    c, org = register()
    prop, tenant, invoice_id = create_property_rent_invoice(c, org)
    bill, _ = create_property_purchase_bill(c, org, prop)
    assert call(c, "post", f"/api/v1/purchase-bills/{bill['id']}/post/", org).status_code == 200
    # tenant pays 100,000 of the 120,000 rent in January
    from apps.accounting.models import Account
    from apps.core.tenancy import organization_context
    with organization_context(org):
        bank = Account.objects.get(account_type="bank")
    r = call(c, "post", "/api/v1/payments/", org, {
        "payment_type": "customer_receipt", "payment_date": "2027-01-15", "contact": tenant["id"],
        "account": str(bank.pk), "amount": "100000", "currency": "NGN",
        "allocations": [{"sales_invoice": invoice_id, "amount": "100000"}]})
    assert r.status_code == 201, r.content
    assert call(c, "post", f"/api/v1/payments/{r.json()['id']}/post/", org).status_code == 200

    agent = make_contact(c, org, "Prime Agents", "other")
    o1 = make_contact(c, org, "Chief Bello", "property_owner")
    o2 = make_contact(c, org, "Mrs Eze", "landlord")
    call(c, "post", f"/api/v1/properties/{prop['id']}/management/", org,
         {"manager": agent["id"], "fee_percent": "10", "start_date": "2027-01-01"})
    call(c, "post", f"/api/v1/properties/{prop['id']}/ownerships/", org, {"owner": o1["id"], "share_percent": "60"})
    call(c, "post", f"/api/v1/properties/{prop['id']}/ownerships/", org, {"owner": o2["id"], "share_percent": "40"})

    body = statement(c, org, prop).json()
    assert body["rent_collected"] == "100000.00"
    assert body["management_fee"] == "10000.00" and body["manager"] == "Prime Agents"
    assert body["expenses"] == "15000.00"
    assert body["net_payable_to_owners"] == "75000.00"
    amounts = {o["owner"]: o["amount"] for o in body["owners"]}
    assert amounts == {"Chief Bello": "45000.00", "Mrs Eze": "30000.00"}
    assert body["unallocated"] == "0.00"


def test_no_management_no_owners_and_validation_and_permissions():
    c, org = register()
    prop, tenant, invoice_id = create_property_rent_invoice(c, org)
    body = statement(c, org, prop).json()
    assert body["management_fee"] == "0.00" and body["owners"] == [] and body["net_payable_to_owners"] == "0.00"
    assert statement(c, org, prop, start="x").status_code == 400
    assert statement(c, org, prop, start="2027-02-01", end="2027-01-01").status_code == 400
    auditor = staff_client(org, "Auditor", "aud@example.com")
    assert statement(auditor, org, prop).status_code == 200
    emp = staff_client(org, "Employee", "emp@example.com")
    assert statement(emp, org, prop).status_code == 403
    other, other_org = register("second@example.com", "Other Co")
    assert statement(other, other_org, prop).status_code == 404
