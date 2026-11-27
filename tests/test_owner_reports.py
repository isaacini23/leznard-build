from datetime import date

import pytest

from tests.test_properties import call, make_property, register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule

pytestmark = pytest.mark.django_db


def test_empty_company_summary_is_all_zero():
    c, org = register()
    body = call(c, "get", "/api/v1/reports/owner-summary/", org).json()
    assert body["units"]["total"] == 0 and body["units"]["occupancy_percent"] == "0.00"
    assert body["rent"]["owed_now"] == "0.00" and body["money"]["net"] == "0.00"
    assert call(c, "get", "/api/v1/reports/rent-owed/", org).json() == []


def test_summary_and_rent_owed_follow_real_activity():
    c, org = register()
    _, unit, _, tenancy = active_tenancy(c, org)
    # a second, vacant unit in the same property
    call(c, "post", f"/api/v1/properties/{unit['property']}/units/", org, {"label": "B2", "unit_type": "flat"})
    schedule = configure_schedule(c, org, tenancy, next_date="2027-01-31", due_days=5)
    charge = call(c, "post", f"/api/v1/rent-schedules/{schedule['id']}/generate/", org, {"post": True}).json()

    owed = call(c, "get", "/api/v1/reports/rent-owed/", org).json()
    assert len(owed) == 1 and owed[0]["amount_due"] == "120000.00" and owed[0]["tenant"] == "Ada Tenant"

    call(c, "post", f"/api/v1/rent-charges/{charge['id']}/pay/", org, {"amount": "50000", "payment_date": "2027-02-03"})
    owed = call(c, "get", "/api/v1/reports/rent-owed/", org).json()
    assert owed[0]["amount_due"] == "70000.00"

    feb = call(c, "get", "/api/v1/reports/owner-summary/?month=2027-02", org).json()
    assert feb["rent"]["collected_this_month"] == "50000.00"
    assert feb["money"]["money_in"] == "50000.00" and feb["money"]["net"] == "50000.00"
    assert feb["rent"]["owed_now"] == "70000.00"
    jan = call(c, "get", "/api/v1/reports/owner-summary/?month=2027-01", org).json()
    assert jan["rent"]["invoiced_this_month"] == "120000.00" and jan["rent"]["collected_this_month"] == "0.00"
    assert jan["units"]["total"] == 2 and jan["units"]["occupied"] == 1 and jan["units"]["vacant"] == 1
    assert jan["units"]["occupancy_percent"] == "50.00"
    assert jan["money"]["customers_owe_you"] == "70000.00"
    assert call(c, "get", "/api/v1/reports/rent-owed/?overdue=1", org).json() == []  # not due until Feb 2027

    from apps.core.tenancy import organization_context
    from apps.properties.owner_reports import owner_summary, rent_roll
    with organization_context(org):
        late = rent_roll(date(2027, 3, 1))
        assert late[0]["status"] == "overdue" and late[0]["days_overdue"] == 24
        assert owner_summary(2027, 3, date(2027, 3, 1))["rent"]["overdue_now"] == "70000.00"
        assert owner_summary(2027, 3, date(2027, 3, 1))["rent"]["tenants_with_arrears"] == 1


def test_validation_permissions_and_isolation():
    c, org = register()
    assert call(c, "get", "/api/v1/reports/owner-summary/?month=2027-13", org).status_code == 400
    assert call(c, "get", "/api/v1/reports/owner-summary/?month=junk", org).status_code == 400
    officer = staff_client(org, "Property officer", "po@example.com")
    assert call(officer, "get", "/api/v1/reports/rent-owed/", org).status_code == 200
    other, other_org = register("second@example.com", "Other Co")
    _, _, _, tenancy = active_tenancy(other, other_org)
    sch = configure_schedule(other, other_org, tenancy)
    call(other, "post", f"/api/v1/rent-schedules/{sch['id']}/generate/", other_org, {"post": True})
    assert call(c, "get", "/api/v1/reports/rent-owed/", org).json() == []
    assert call(c, "get", "/api/v1/reports/owner-summary/?month=2027-01", org).json()["units"]["total"] == 0
