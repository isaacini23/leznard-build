from datetime import date, timedelta

import pytest
from django.core.management import call_command

from apps.accounting.models import Account
from apps.core.tenancy import organization_context
from tests.test_owner_rent_actions import call
from tests.test_properties import make_contact, make_property, register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule
from tests.test_property_profitability import create_property_rent_invoice

pytestmark = pytest.mark.django_db


def setup_rent(client, org, *, fee=None):
    """A tenancy with one posted rent invoice (120,000, due 5 days after 2027-01-31)."""
    _, unit, tenant, tenancy = active_tenancy(client, org)
    sched = configure_schedule(client, org, tenancy, next_date="2027-01-31")
    if fee:
        r = call(client, "patch", f"/api/v1/tenancies/{tenancy['id']}/rent-schedule/", org, fee)
        assert r.status_code == 200, r.content
    charge = call(client, "post", f"/api/v1/rent-schedules/{sched['id']}/generate/", org, {"post": True}).json()
    return unit, tenant, tenancy, charge


# ---- late fees ----------------------------------------------------------------------------


def test_late_fee_charged_once_after_grace_and_only_when_configured():
    c, org = register()
    unit, tenant, tenancy, charge = setup_rent(c, org, fee={"late_fee_percent": "5", "late_fee_flat": "1000", "late_fee_grace_days": 3})
    due = date(2027, 2, 5)
    from apps.properties.tenant_space import apply_late_fees
    with organization_context(org):
        assert apply_late_fees(due + timedelta(days=3))["charged"] == 0  # still inside the grace period
        r = apply_late_fees(due + timedelta(days=4), post=True)
        assert r["charged"] == 1 and r["fees"][0]["fee"] == "7000.00"  # 1000 + 5% of 120,000
        assert apply_late_fees(due + timedelta(days=30))["charged"] == 0  # never twice


def test_no_fee_when_not_configured_or_already_paid():
    c, org = register()
    unit, tenant, tenancy, charge = setup_rent(c, org)  # no fee configured
    from apps.properties.tenant_space import apply_late_fees
    with organization_context(org):
        assert apply_late_fees(date(2027, 6, 1))["charged"] == 0
    c2, org2 = register("second@example.com", "Other Co")
    u, t, ten, ch = setup_rent(c2, org2, fee={"late_fee_flat": "500"})
    call(c2, "post", f"/api/v1/rent-charges/{ch['id']}/pay/", org2, {"account": str(_bank(org2))})
    with organization_context(org2):
        assert apply_late_fees(date(2027, 6, 1))["charged"] == 0  # paid in full


def _bank(org):
    with organization_context(org):
        return Account.objects.filter(account_type="bank").first().pk


def test_late_fee_api_and_command_and_permissions():
    c, org = register()
    setup_rent(c, org, fee={"late_fee_flat": "500"})
    r = call(c, "post", "/api/v1/rent-schedules/apply-late-fees/", org, {})
    assert r.status_code == 200 and r.json()["charged"] == 0  # not overdue yet in real time
    call_command("generate_rent_invoices", "--late-fees", "--date", "2027-03-01")
    emp = staff_client(org, "Employee", "emp@example.com")
    assert call(emp, "post", "/api/v1/rent-schedules/apply-late-fees/", org, {}).status_code == 403


# ---- reminders ------------------------------------------------------------------------------


def test_reminders_have_message_text_for_overdue_and_upcoming():
    c, org = register()
    setup_rent(c, org)
    from apps.properties.tenant_space import rent_reminders
    with organization_context(org):
        up = rent_reminders(date(2027, 2, 3), days_before=3)
        assert up[0]["kind"] == "upcoming" and "120,000.00" in up[0]["message"] and "Ada Tenant" in up[0]["message"]
        late = rent_reminders(date(2027, 2, 20))
        assert late[0]["kind"] == "overdue" and "15 day(s) overdue" in late[0]["message"]
        assert rent_reminders(date(2027, 1, 1), days_before=3) == []
    assert call(c, "get", "/api/v1/reports/rent-reminders/?days_before=x", org).status_code == 400
    assert call(c, "get", "/api/v1/reports/rent-reminders/", org).status_code == 200


# ---- owner payouts --------------------------------------------------------------------------


def test_owner_payout_posts_to_ledger_and_cannot_exceed_what_is_owed():
    c, org = register()
    prop, tenant, invoice_id = create_property_rent_invoice(c, org)
    bank = _bank(org)
    r = call(c, "post", "/api/v1/payments/", org, {
        "payment_type": "customer_receipt", "payment_date": "2027-01-15", "contact": tenant["id"],
        "account": str(bank), "amount": "100000", "currency": "NGN",
        "allocations": [{"sales_invoice": invoice_id, "amount": "100000"}]})
    call(c, "post", f"/api/v1/payments/{r.json()['id']}/post/", org)
    o1 = make_contact(c, org, "Chief Bello", "property_owner")
    o2 = make_contact(c, org, "Mrs Eze", "landlord")
    call(c, "post", f"/api/v1/properties/{prop['id']}/ownerships/", org, {"owner": o1["id"], "share_percent": "60"})
    call(c, "post", f"/api/v1/properties/{prop['id']}/ownerships/", org, {"owner": o2["id"], "share_percent": "40"})
    url = f"/api/v1/properties/{prop['id']}/owner-payouts/"
    period = {"period_start": "2027-01-01", "period_end": "2027-01-31", "account": str(bank)}
    assert call(c, "post", url, org, {**period, "owner": o1["id"], "amount": "70000"}).status_code == 400  # owed 60,000
    ok = call(c, "post", url, org, {**period, "owner": o1["id"], "amount": "25000", "payout_date": "2027-02-02"})
    assert ok.status_code == 201, ok.content
    st = call(c, "get", f"/api/v1/properties/{prop['id']}/owner-statement/?start=2027-01-01&end=2027-01-31", org).json()
    bello = next(o for o in st["owners"] if o["owner"] == "Chief Bello")
    assert bello["amount"] == "60000.00" and bello["paid"] == "25000.00" and bello["outstanding"] == "35000.00"
    rest = call(c, "post", url, org, {**period, "owner": o1["id"]})  # default: all that is still owed
    assert rest.status_code == 201 and rest.json()["amount"] == "35000.00"
    assert call(c, "post", url, org, {**period, "owner": o1["id"]}).status_code == 400  # nothing left
    stranger = make_contact(c, org, "Random", "property_owner")
    assert call(c, "post", url, org, {**period, "owner": stranger["id"]}).status_code == 400  # not an owner
    assert len(call(c, "get", url, org).json()) == 2
    emp = staff_client(org, "Employee", "emp@example.com")
    assert call(emp, "post", url, org, {**period, "owner": o2["id"]}).status_code == 403
    tb = call(c, "get", "/api/v1/reports/trial-balance/?as_of=2027-12-31", org).json()
    rows = {x["code"]: x for x in tb["rows"]}
    assert rows["3300"]["debit"] == "60000.00" and tb["balanced"]


# ---- tenant portal --------------------------------------------------------------------------


def portal_setup():
    """A landlord company with a tenant who has accepted a portal invitation."""
    c, org = register()
    unit, tenant, tenancy, charge = setup_rent(c, org)
    invite = call(c, "post", f"/api/v1/contacts/{tenant['id']}/portal-invite/", org, {})
    assert invite.status_code == 201, invite.content
    tclient, _ = register("ada@example.com", "Ada Personal")
    acc = tclient.post("/api/v1/portal/accept/", {"token": invite.json()["token"]}, content_type="application/json")
    assert acc.status_code == 201, acc.content
    return c, org, unit, tenant, tenancy, charge, tclient, invite.json()["token"]


def test_tenant_sees_only_their_own_rent_deposit_and_can_report_a_repair():
    c, org, unit, tenant, tenancy, charge, t, token = portal_setup()
    home = t.get("/api/v1/portal/").json()
    assert home["tenant"] == "Ada Tenant" and home["tenancies"][0]["unit"] == "A1"
    rent = t.get("/api/v1/portal/rent/").json()
    assert len(rent) == 1 and rent[0]["balance"] == "120000.00" and rent[0]["status"] in ("overdue", "due")
    call(c, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", org, {"account": str(_bank(org)), "amount": "20000"})
    assert t.get("/api/v1/portal/rent/").json()[0]["balance"] == "100000.00"
    assert t.get("/api/v1/portal/deposit/").json()["held"] == "0.00"
    rep = t.post("/api/v1/portal/maintenance/", {"unit": unit["id"], "title": "Leaking roof", "priority": "high"},
                 content_type="application/json")
    assert rep.status_code == 201, rep.content
    assert t.get("/api/v1/portal/maintenance/").json()[0]["title"] == "Leaking roof"
    # the landlord sees it as a normal maintenance request reported by the tenant
    mine = call(c, "get", "/api/v1/maintenance/?active=1", org).json()
    assert mine[0]["reported_by"] == tenant["id"] and mine[0]["priority"] == "high"
    # a tenant cannot report on a unit they do not rent, or without a title
    assert t.post("/api/v1/portal/maintenance/", {"unit": unit["id"], "title": " "}, content_type="application/json").status_code == 400
    other = call(c, "post", f"/api/v1/properties/{unit['property']}/units/", org, {"label": "Z9", "unit_type": "flat"}).json()
    assert t.post("/api/v1/portal/maintenance/", {"unit": other["id"], "title": "x"}, content_type="application/json").status_code == 400


def test_portal_is_private_to_the_tenant_and_invites_are_single_use():
    c, org, unit, tenant, tenancy, charge, t, token = portal_setup()
    # the landlord's staff and strangers have no portal access
    assert c.get("/api/v1/portal/").status_code == 400
    stranger, _ = register("stranger@example.com", "Stranger Co")
    assert stranger.get("/api/v1/portal/rent/").status_code == 400
    # the code cannot be used again, by another user or the same one
    assert stranger.post("/api/v1/portal/accept/", {"token": token}, content_type="application/json").status_code == 400
    assert t.post("/api/v1/portal/accept/", {"token": token}, content_type="application/json").status_code == 400
    assert stranger.post("/api/v1/portal/accept/", {"token": "junk"}, content_type="application/json").status_code == 400
    # a portal user cannot use company endpoints by sending the company header
    assert t.get("/api/v1/tenancies/", HTTP_X_ORGANIZATION_ID=org).status_code in (401, 403)
    assert t.get("/api/v1/contacts/", HTTP_X_ORGANIZATION_ID=org).status_code in (401, 403)


def test_second_tenant_data_never_leaks_and_invite_rules():
    c, org, unit, tenant, tenancy, charge, t, token = portal_setup()
    # another tenant in the same company with their own rent
    _, u2, t2, ten2 = None, None, make_contact(c, org, "Bola Tenant", "tenant"), None
    prop = make_property(c, org, "Second Court").json()
    unit2 = call(c, "post", f"/api/v1/properties/{prop['id']}/units/", org, {"label": "B1", "unit_type": "flat"}).json()
    ten2 = call(c, "post", "/api/v1/tenancies/", org, {"unit": unit2["id"], "tenant": t2["id"], "start_date": "2027-01-01", "rent_amount": "50000"}).json()
    call(c, "post", f"/api/v1/tenancies/{ten2['id']}/activate/", org)
    assert len(t.get("/api/v1/portal/").json()["tenancies"]) == 1
    assert all(r["unit"] == "A1" for r in t.get("/api/v1/portal/rent/").json())
    # invitations: tenants only, one portal per tenant
    owner = make_contact(c, org, "Chief", "property_owner")
    assert call(c, "post", f"/api/v1/contacts/{owner['id']}/portal-invite/", org, {}).status_code == 400
    assert call(c, "post", f"/api/v1/contacts/{tenant['id']}/portal-invite/", org, {}).status_code == 400  # already linked
    emp = staff_client(org, "Employee", "emp2@example.com")
    assert call(emp, "post", f"/api/v1/contacts/{t2['id']}/portal-invite/", org, {}).status_code == 403
