import pytest

from tests.test_owner_rent_actions import call
from tests.test_properties import register
from tests.test_rent_management import active_tenancy, configure_schedule

pytestmark = pytest.mark.django_db


def charges(client, org, tenancy):
    return call(client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-charges/", org).json()


def test_leaving_mid_period_bills_the_part_period_pro_rata():
    c, org = register()
    _, unit, _, tenancy = active_tenancy(c, org, start="2027-01-01")
    configure_schedule(c, org, tenancy, next_date="2027-01-01")
    # leaves 10 March: Jan and Feb billed in full, 1-10 March = 10/31 of one month's rent
    r = call(c, "post", f"/api/v1/tenancies/{tenancy['id']}/end/", org, {"end_date": "2027-03-10"})
    assert r.status_code == 200, r.content
    got = sorted(charges(c, org, tenancy), key=lambda x: x["period_start"])
    assert [(x["period_start"], x["period_end"]) for x in got] == [
        ("2027-01-01", "2027-01-31"), ("2027-02-01", "2027-02-28"), ("2027-03-01", "2027-03-10")]
    assert got[2]["amount_due"] == "38709.6800"
    assert call(c, "get", f"/api/v1/units/{unit['id']}/", org).json()["occupancy_status"] == "vacant"
    # schedule is closed, nothing more to generate
    assert call(c, "post", "/api/v1/rent-schedules/generate-due/", org, {}).json()["generated"] == 0


def test_end_on_last_day_of_period_bills_no_extra_and_opt_out_works():
    c, org = register()
    _, _, _, tenancy = active_tenancy(c, org, start="2027-01-01")
    configure_schedule(c, org, tenancy, next_date="2027-01-01")
    call(c, "post", f"/api/v1/tenancies/{tenancy['id']}/end/", org, {"end_date": "2027-01-31"})
    got = charges(c, org, tenancy)
    assert len(got) == 1 and got[0]["period_end"] == "2027-01-31"

    c2, org2 = register("second@example.com", "Other Co")
    _, _, _, t2 = active_tenancy(c2, org2, start="2027-01-01")
    configure_schedule(c2, org2, t2, next_date="2027-01-01")
    call(c2, "post", f"/api/v1/tenancies/{t2['id']}/end/", org2, {"end_date": "2027-03-10", "bill_final_rent": False})
    assert charges(c2, org2, t2) == []
