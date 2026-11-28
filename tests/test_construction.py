import pytest

from apps.accounting.models import Account
from apps.accounting.services import create_default_chart_of_accounts
from apps.contacts.models import Contact
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization
from apps.sales.models import PurchaseBill, PurchaseBillLine
from tests.test_properties import call, make_contact, register, staff_client

pytestmark = pytest.mark.django_db


def company():
    c, org = register()
    organization = Organization.objects.get(pk=org)
    with organization_context(org):
        create_default_chart_of_accounts(organization)
        bank = Account.objects.filter(account_type="bank").first().pk
    return c, org, str(bank)


def project(c, org, **extra):
    client = make_contact(c, org, "Lagos Developers", "customer")
    body = {"code": "P-001", "name": "Ikoyi Block", "client": client["id"], "contract_value": "10000000", **extra}
    r = call(c, "post", "/api/v1/projects/", org, body)
    assert r.status_code == 201, r.content
    return r.json()


def summary(c, org, pid):
    return call(c, "get", f"/api/v1/projects/{pid}/summary/", org).json()


def tb(c, org):
    rows = call(c, "get", "/api/v1/reports/trial-balance/?as_of=2099-12-31", org).json()
    return {r["name"]: r for r in rows["rows"]}, rows["balanced"]


def test_project_budget_variations_and_progress_billing_with_cap():
    c, org, bank = company()
    p = project(c, org)
    base = f"/api/v1/projects/{p['id']}"
    assert call(c, "post", "/api/v1/projects/", org, {"code": "P-001", "name": "dup", "client": p["client"], "contract_value": "1"}).status_code == 400
    plain = make_contact(c, org, "Not A Customer", "supplier")
    assert call(c, "post", "/api/v1/projects/", org, {"code": "P-2", "name": "x", "client": plain["id"]}).status_code == 400
    for ct, desc, amt in (("materials", "Cement and steel", "4000000"), ("labour", "Site labour", "2000000")):
        assert call(c, "post", f"{base}/budget/", org, {"cost_type": ct, "description": desc, "budget_amount": amt}).status_code == 201
    assert call(c, "post", f"{base}/budget/", org, {"cost_type": "labour", "description": "Site labour", "budget_amount": "1"}).status_code == 400
    v = call(c, "post", f"{base}/variations/", org, {"description": "Extra floor", "amount": "2000000"}).json()
    assert call(c, "post", f"/api/v1/variations/{v['id']}/approve/", org, {}).json()["status"] == "approved"
    assert call(c, "post", f"/api/v1/variations/{v['id']}/approve/", org, {}).status_code == 400  # decided once
    s = summary(c, org, p["id"])
    assert s["contract"]["current_value"] == "12000000.00" and s["contract"]["approved_variations"] == "2000000.00"
    assert s["costs"]["budget_total"] == "6000000.00" and s["profit"]["expected_margin"] == "6000000.00"

    inv = call(c, "post", f"{base}/invoices/", org, {"amount": "3000000", "description": "Foundation done"})
    assert inv.status_code == 201 and inv.json()["status"] == "posted", inv.content
    assert call(c, "post", f"{base}/invoices/", org, {"amount": "9000001"}).status_code == 400  # only 9,000,000 left
    assert call(c, "post", f"{base}/invoices/", org, {"amount": "-5"}).status_code == 400
    s = summary(c, org, p["id"])
    assert s["contract"]["billed_to_date"] == "3000000.00" and s["contract"]["left_to_bill"] == "9000000.00"
    assert s["contract"]["client_owes"] == "3000000.00"


def test_costs_link_to_budget_lines_and_drafts_do_not_count():
    c, org, bank = company()
    p = project(c, org)
    base = f"/api/v1/projects/{p['id']}"
    line = call(c, "post", f"{base}/budget/", org, {"cost_type": "materials", "description": "Cement", "budget_amount": "100000"}).json()
    supplier = make_contact(c, org, "Dangote Supplies", "supplier")
    with organization_context(org):
        expense = Account.objects.filter(account_type="cost_of_sales").first()
        bill = PurchaseBill.objects.create(
            supplier=Contact.unscoped.get(pk=supplier["id"]), number="B-1", document_date="2027-03-01",
            due_date="2027-03-31", currency="NGN")
        PurchaseBillLine.objects.create(bill=bill, description="Cement", quantity=1, unit_price="120000", expense_account=expense)
    link = call(c, "post", f"{base}/costs/", org, {"purchase_bill": str(bill.pk), "budget_line": line["id"]})
    assert link.status_code == 201, link.content
    assert call(c, "post", f"{base}/costs/", org, {"purchase_bill": str(bill.pk)}).status_code == 400  # already charged
    assert summary(c, org, p["id"])["costs"]["cost_to_date"] == "0.00"  # still a draft
    assert call(c, "post", f"/api/v1/purchase-bills/{bill.pk}/post/", org).status_code == 200
    s = summary(c, org, p["id"])
    assert s["costs"]["cost_to_date"] == "120000.00" and s["lines_over_budget"] == 1
    assert s["budget_lines"][0]["actual"] == "120000.00" and s["budget_lines"][0]["percent_used"] == "120.00"
    assert s["profit"]["profit_to_date"] == "-120000.00"
    other = project(c, org, code="P-002")  # a bill can't be charged to a second project, nor a line of another
    assert call(c, "post", f"/api/v1/projects/{other['id']}/costs/", org, {"purchase_bill": str(bill.pk)}).status_code == 400


def test_subcontract_claim_with_retention_end_to_end():
    c, org, bank = company()
    p = project(c, org)
    base = f"/api/v1/projects/{p['id']}"
    line = call(c, "post", f"{base}/budget/", org, {"cost_type": "subcontractors", "description": "Electrical", "budget_amount": "1200000"}).json()
    sub_only = make_contact(c, org, "Volt Ltd", "subcontractor")
    sub = call(c, "post", f"{base}/subcontracts/", org, {"subcontractor": sub_only["id"], "description": "Electrical works",
                                                      "contract_sum": "1000000", "retention_percent": "10", "budget_line": line["id"]})
    assert sub.status_code == 201, sub.content
    sid = sub.json()["id"]
    claim = call(c, "post", f"/api/v1/subcontracts/{sid}/claims/", org, {"gross_amount": "400000", "description": "Claim 1"})
    assert claim.status_code == 201 and claim.json()["retention_amount"] == "40000.00" and claim.json()["number"] == 1
    cid = claim.json()["id"]
    assert call(c, "post", f"/api/v1/subcontracts/{sid}/claims/", org, {"gross_amount": "700000"}).status_code == 400  # > what is left
    # the contractor has no Supplier role yet, so it cannot be billed or paid
    assert call(c, "post", f"/api/v1/claims/{cid}/certify/", org, {}).status_code == 400
    from apps.contacts.models import ContactRole
    with organization_context(org):
        ContactRole.objects.create(contact=Contact.objects.get(pk=sub_only["id"]), role="supplier")
    cert = call(c, "post", f"/api/v1/claims/{cid}/certify/", org, {})
    assert cert.status_code == 200 and cert.json()["status"] == "certified" and cert.json()["purchase_bill"], cert.content
    assert call(c, "post", f"/api/v1/claims/{cid}/certify/", org, {}).status_code == 400
    s = summary(c, org, p["id"])
    assert s["costs"]["cost_to_date"] == "400000.00" and s["costs"]["retention_held_from_subcontractors"] == "40000.00"
    assert s["budget_lines"][0]["actual"] == "400000.00"

    assert call(c, "post", f"/api/v1/claims/{cid}/pay/", org, {}).status_code == 400  # account required
    assert call(c, "post", f"/api/v1/claims/{cid}/release-retention/", org, {"account": bank}).status_code == 400  # net unpaid
    paid = call(c, "post", f"/api/v1/claims/{cid}/pay/", org, {"account": bank, "amount": "100000"})
    assert paid.status_code == 200, paid.content
    assert call(c, "post", f"/api/v1/claims/{cid}/pay/", org, {"account": bank, "amount": "300000"}).status_code == 400  # only 260,000 left
    assert call(c, "post", f"/api/v1/claims/{cid}/pay/", org, {"account": bank}).status_code == 200  # the rest of the net
    assert call(c, "post", f"/api/v1/claims/{cid}/pay/", org, {"account": bank}).status_code == 400  # retention held back
    rows, balanced = tb(c, org)
    assert rows["Accounts Payable"]["credit"] == "40000.00" and balanced  # exactly the retention
    rel = call(c, "post", f"/api/v1/claims/{cid}/release-retention/", org, {"account": bank, "amount": "15000"})
    assert rel.status_code == 200 and rel.json()["retention_held"] == "25000.00"
    assert call(c, "post", f"/api/v1/claims/{cid}/release-retention/", org, {"account": bank, "amount": "30000"}).status_code == 400
    assert call(c, "post", f"/api/v1/claims/{cid}/release-retention/", org, {"account": bank}).json()["retention_held"] == "0.00"
    rows, balanced = tb(c, org)
    assert "Accounts Payable" not in rows and balanced
    assert summary(c, org, p["id"])["costs"]["retention_held_from_subcontractors"] == "0.00"
    # claims can be cancelled only while draft
    c2 = call(c, "post", f"/api/v1/subcontracts/{sid}/claims/", org, {"gross_amount": "100000"}).json()
    assert call(c, "post", f"/api/v1/claims/{c2['id']}/cancel/", org, {}).json()["status"] == "cancelled"
    assert call(c, "post", f"/api/v1/claims/{cid}/cancel/", org, {}).status_code == 400
    # a subcontractor needs the Subcontractor role
    stranger = make_contact(c, org, "Random", "customer")
    assert call(c, "post", f"{base}/subcontracts/", org, {"subcontractor": stranger["id"], "description": "x", "contract_sum": "1"}).status_code == 400


def test_permissions_manager_posting_and_isolation():
    c, org, bank = company()
    p = project(c, org)
    base = f"/api/v1/projects/{p['id']}"
    emp = staff_client(org, "Employee", "emp@example.com")
    assert call(emp, "get", "/api/v1/projects/", org).status_code == 403
    aud = staff_client(org, "Auditor", "aud@example.com")
    assert call(aud, "get", f"{base}/summary/", org).status_code == 200
    assert call(aud, "post", "/api/v1/projects/", org, {"code": "X", "name": "x", "client": p["client"]}).status_code == 403
    mgr = staff_client(org, "Manager", "mgr@example.com")
    assert call(mgr, "post", f"{base}/budget/", org, {"cost_type": "labour", "description": "Crew", "budget_amount": "5"}).status_code == 201
    draft = call(c, "post", f"{base}/invoices/", org, {"amount": "1000", "post": False}).json()
    assert draft["status"] == "draft"
    # Managers can post documents themselves: nothing waits on an accountant
    assert call(mgr, "post", f"/api/v1/sales-invoices/{draft['invoice']}/post/", org).status_code == 200
    acct = staff_client(org, "Accountant", "acct@example.com")
    assert call(acct, "get", "/api/v1/projects/", org).status_code == 200
    assert call(acct, "post", f"{base}/invoices/", org, {"amount": "1000"}).status_code == 201
    other, other_org, _ = None, None, None
    o, oorg = register("second@example.com", "Other Co")
    assert call(o, "get", f"{base}/summary/", oorg).status_code == 404
    assert call(o, "post", f"{base}/invoices/", oorg, {"amount": "1"}).status_code == 404
    assert call(c, "delete", f"/api/v1/projects/{p['id']}/", org).status_code == 405
