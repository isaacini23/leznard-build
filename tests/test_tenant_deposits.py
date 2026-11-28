from decimal import Decimal

import pytest

from tests.test_owner_rent_actions import call
from tests.test_properties import register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule

pytestmark = pytest.mark.django_db


def make(client, org):
    _, _, _, tenancy = active_tenancy(client, org)
    from apps.core.tenancy import organization_context
    from apps.properties.models import Tenancy

    with organization_context(org):
        Tenancy.objects.filter(pk=tenancy["id"]).update(deposit_amount=Decimal("200000"))
    configure_schedule(client, org, tenancy)  # also creates the chart of accounts
    return tenancy


def deposit(client, org, tid):
    return call(client, "get", f"/api/v1/tenancies/{tid}/deposit/", org).json()


def bank_account_id(client, org):
    from apps.accounting.models import Account
    from apps.core.tenancy import organization_context

    with organization_context(org):
        return str(Account.objects.get(code="1010").pk)


def test_deposit_lifecycle_posts_to_ledger():
    c, org = register()
    t = make(c, org)
    tid = t["id"]
    account_id = bank_account_id(c, org)
    assert call(c, "post", f"/api/v1/tenancies/{tid}/deposit/receive/", org, {"amount": "200000", "date": "2027-01-31", "account": account_id}).status_code == 201
    d = deposit(c, org, tid)
    assert d["received"] == "200000.00" and d["held"] == "200000.00"
    # cannot refund or deduct while the tenancy is still running
    assert call(c, "post", f"/api/v1/tenancies/{tid}/deposit/refund/", org, {}).status_code == 400
    assert call(c, "post", f"/api/v1/tenancies/{tid}/end/", org, {"end_date": "2027-06-30"}).status_code == 200
    bad = call(c, "post", f"/api/v1/tenancies/{tid}/deposit/deduct/", org, {"amount": "250000", "reason": "x"})
    assert bad.status_code == 400
    assert call(c, "post", f"/api/v1/tenancies/{tid}/deposit/deduct/", org, {"amount": "30000"}).status_code == 400  # needs a reason
    ok = call(c, "post", f"/api/v1/tenancies/{tid}/deposit/deduct/", org, {"amount": "30000", "reason": "Broken window"})
    assert ok.status_code == 201, ok.content
    refund = call(c, "post", f"/api/v1/tenancies/{tid}/deposit/refund/", org, {"date": "2027-07-05", "account": account_id})
    assert refund.status_code == 201, refund.content
    d = refund.json()
    assert d["deducted"] == "30000.00" and d["refunded"] == "170000.00" and d["held"] == "0.00"
    assert call(c, "post", f"/api/v1/tenancies/{tid}/deposit/refund/", org, {}).status_code == 400  # nothing left
    # the books: ledger shows 30,000 kept as income and the liability cleared
    tb = call(c, "get", "/api/v1/reports/trial-balance/?as_of=2027-12-31", org).json()
    rows = {r["code"]: r for r in tb["rows"]}
    assert "2300" not in rows  # liability fully cleared
    assert rows["4900"]["credit"] == "30000.00" and rows["1010"]["debit"] == "30000.00" and tb["balanced"]


def test_deposit_follows_a_renewal_and_permissions():
    c, org = register()
    t = make(c, org)
    account_id = bank_account_id(c, org)
    call(c, "post", f"/api/v1/tenancies/{t['id']}/deposit/receive/", org, {"amount": "200000", "date": "2027-01-31", "account": account_id})
    sched = call(c, "get", f"/api/v1/tenancies/{t['id']}/rent-schedule/", org).json()
    call(c, "post", f"/api/v1/rent-schedules/{sched['id']}/generate/", org, {})
    new = call(c, "post", f"/api/v1/tenancies/{t['id']}/renew/", org, {"start_date": "2027-02-28"}).json()
    assert deposit(c, org, new["id"])["held"] == "200000.00"
    auditor = staff_client(org, "Auditor", "aud@example.com")
    assert call(auditor, "get", f"/api/v1/tenancies/{new['id']}/deposit/", org).status_code == 200
    assert call(auditor, "post", f"/api/v1/tenancies/{new['id']}/deposit/receive/", org, {"amount": "1"}).status_code == 403
    other, other_org = register("second@example.com", "Other Co")
    assert call(other, "get", f"/api/v1/tenancies/{new['id']}/deposit/", other_org).status_code == 404
    assert call(c, "post", f"/api/v1/tenancies/{new['id']}/deposit/receive/", org, {"amount": "-5"}).status_code == 400

def test_deposit_requires_explicit_account_and_rejects_rounded_zero_or_overpayment():
    c, org = register()
    t = make(c, org)
    tid = t["id"]
    account_id = bank_account_id(c, org)

    missing_account = call(
        c, "post", f"/api/v1/tenancies/{tid}/deposit/receive/", org,
        {"amount": "200000"},
    )
    assert missing_account.status_code == 400
    assert deposit(c, org, tid)["received"] == "0.00"

    sub_kobo = call(
        c, "post", f"/api/v1/tenancies/{tid}/deposit/receive/", org,
        {"amount": "0.001", "account": account_id},
    )
    assert sub_kobo.status_code == 400

    fractional_kobo = call(
        c, "post", f"/api/v1/tenancies/{tid}/deposit/receive/", org,
        {"amount": "1.001", "account": account_id},
    )
    assert fractional_kobo.status_code == 400

    overpayment = call(
        c, "post", f"/api/v1/tenancies/{tid}/deposit/receive/", org,
        {"amount": "200001", "account": account_id},
    )
    assert overpayment.status_code == 400
    assert deposit(c, org, tid)["received"] == "0.00"
