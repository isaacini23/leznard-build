import pytest

from tests.test_properties import call, register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule

pytestmark = pytest.mark.django_db


def generated_charge(client, org, **gen):
    _, _, tenant, tenancy = active_tenancy(client, org)
    schedule = configure_schedule(client, org, tenancy)
    r = call(client, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org, gen or None)
    assert r.status_code == 201, r.content
    return r.json(), tenancy


def charges(client, org, tenancy):
    return call(client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-charges/", org).json()


def test_generate_can_post_the_invoice_in_one_step():
    c, org = register()
    charge, _ = generated_charge(c, org, post=True)
    assert charge["invoice_status"] == "posted"


def test_post_endpoint_and_double_post_refused():
    c, org = register()
    charge, _ = generated_charge(c, org)
    assert charge["invoice_status"] == "draft"
    ok = call(c, "post", f"/api/v1/rent-charges/{charge['id']}/post/", org)
    assert ok.status_code == 200 and ok.json()["invoice_status"] == "posted"
    assert call(c, "post", f"/api/v1/rent-charges/{charge['id']}/post/", org).status_code == 400


def test_pay_posts_invoice_and_receipt_and_defaults_to_full_amount():
    c, org = register()
    charge, tenancy = generated_charge(c, org)  # still a draft invoice
    paid = call(c, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", org, {})
    assert paid.status_code == 200, paid.content
    body = paid.json()
    assert body["status"] == "paid" and body["amount_due"] == "0.0000" and body["invoice_status"] == "posted"
    assert call(c, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", org, {}).status_code == 400


def test_part_payments_accumulate_and_overpaying_is_refused():
    c, org = register()
    charge, tenancy = generated_charge(c, org, post=True)
    url = f"/api/v1/rent-charges/{charge['id']}/pay/"
    first = call(c, "post", url, org, {"amount": "50000", "payment_date": "2027-02-02", "method": "cash"})
    assert first.status_code == 200, first.content
    assert first.json()["status"] == "partially_paid" and first.json()["amount_due"] == "70000.0000"
    assert call(c, "post", url, org, {"amount": "80000"}).status_code == 400
    assert call(c, "post", url, org, {"amount": "0"}).status_code == 400
    assert call(c, "post", url, org, {"amount": "abc"}).status_code == 400
    assert call(c, "post", url, org, {"payment_date": "nope"}).status_code == 400
    done = call(c, "post", url, org, {"amount": "70000"})
    assert done.json()["status"] == "paid"
    assert charges(c, org, tenancy)[0]["amount_paid"] == "120000.0000"


def test_permissions_and_company_isolation():
    c, org = register()
    charge, _ = generated_charge(c, org)
    auditor = staff_client(org, "Auditor", "aud@example.com")
    assert call(auditor, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", org, {}).status_code == 403
    assert call(auditor, "post", f"/api/v1/rent-charges/{charge['id']}/post/", org).status_code == 403
    officer = staff_client(org, "Property officer", "po@example.com")
    assert call(officer, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", org, {"amount": "1000"}).status_code == 200
    other, other_org = register("second@example.com", "Other Co")
    assert call(other, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", other_org, {}).status_code == 404
