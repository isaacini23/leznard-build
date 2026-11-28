import pytest

from tests.test_owner_rent_actions import call
from tests.test_properties import register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule

pytestmark = pytest.mark.django_db


def charges(client, org, tenancy):
    return call(client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-charges/", org).json()


def test_renewal_with_rent_increase_keeps_unit_occupied_and_billing_continuous():
    c, org = register()
    _, unit, _, tenancy = active_tenancy(c, org)
    sched = configure_schedule(c, org, tenancy, next_date="2027-01-31")
    call(c, "post", f"/api/v1/rent-schedules/{sched['id']}/generate/", org, {})  # Jan 31 at 120,000; next Feb 28
    r = call(c, "post", f"/api/v1/tenancies/{tenancy['id']}/renew/", org,
             {"start_date": "2027-02-28", "end_date": "2027-12-31", "rent_amount": "150000"})
    assert r.status_code == 201, r.content
    new = r.json()
    assert new["status"] == "active" and new["rent_amount"] == "150000.00" and new["renewed_from"] == tenancy["id"]
    old = call(c, "get", f"/api/v1/tenancies/{tenancy['id']}/", org).json()
    assert old["status"] == "ended" and old["end_date"] == "2027-02-27" and old["end_reason"] == "Renewed"
    assert call(c, "get", f"/api/v1/units/{unit['id']}/", org).json()["occupancy_status"] == "occupied"
    # the new tenancy is billed from where the old one stopped, at the new rent
    new_sched = call(c, "get", f"/api/v1/tenancies/{new['id']}/rent-schedule/", org).json()
    assert new_sched["next_invoice_date"] == "2027-02-28"
    g = call(c, "post", f"/api/v1/rent-schedules/{new_sched['id']}/generate/", org, {})
    assert g.status_code == 201, g.content
    assert charges(c, org, new)[0]["period_start"] == "2027-02-28"
    assert [x["period_start"] for x in charges(c, org, tenancy)] == ["2027-01-31"]
    # cannot renew twice, and only active tenancies renew
    assert call(c, "post", f"/api/v1/tenancies/{tenancy['id']}/renew/", org, {"start_date": "2027-06-01"}).status_code == 400


def test_renewal_refused_when_earlier_periods_are_unbilled_and_for_bad_input():
    c, org = register()
    _, _, _, tenancy = active_tenancy(c, org)
    configure_schedule(c, org, tenancy, next_date="2027-01-31")
    url = f"/api/v1/tenancies/{tenancy['id']}/renew/"
    assert call(c, "post", url, org, {"start_date": "2027-03-31"}).status_code == 400  # Jan/Feb not invoiced yet
    assert call(c, "post", url, org, {"start_date": "2027-01-31"}).status_code == 400  # not after start
    assert call(c, "post", url, org, {"start_date": "nope"}).status_code == 400
    assert call(c, "post", url, org, {"start_date": "2027-02-15", "end_date": "2027-01-01"}).status_code == 400
    assert call(c, "post", url, org, {"start_date": "2027-02-15", "rent_amount": "abc"}).status_code == 400
    assert call(c, "post", url, org, {"start_date": "2027-02-15", "rent_frequency": "weekly"}).status_code == 400


def test_renewal_permissions_and_isolation():
    c, org = register()
    _, _, _, tenancy = active_tenancy(c, org)
    url = f"/api/v1/tenancies/{tenancy['id']}/renew/"
    auditor = staff_client(org, "Auditor", "aud@example.com")
    assert call(auditor, "post", url, org, {"start_date": "2027-02-15"}).status_code == 403
    other, other_org = register("second@example.com", "Other Co")
    assert call(other, "post", url, other_org, {"start_date": "2027-02-15"}).status_code == 400

def test_renewal_cannot_start_midway_through_an_unbilled_rent_period():
    c, org = register()
    _, _, _, tenancy = active_tenancy(c, org)
    schedule = configure_schedule(c, org, tenancy, next_date="2027-01-31")
    first = call(c, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org, {})
    assert first.status_code == 201

    # The next unbilled period starts on 28 February. A 15 February renewal
    # would leave the first half of that period billed at the old rent.
    url = f"/api/v1/tenancies/{tenancy['id']}/renew/"
    mismatch = call(c, "post", url, org, {
        "start_date": "2027-02-15", "rent_amount": "150000"
    })
    assert mismatch.status_code == 400
    assert call(c, "get", f"/api/v1/tenancies/{tenancy['id']}/", org).json()["status"] == "active"

    aligned = call(c, "post", url, org, {
        "start_date": "2027-02-28", "rent_amount": "150000"
    })
    assert aligned.status_code == 201, aligned.content
