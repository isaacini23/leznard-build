from datetime import date

import pytest
from django.core.management import call_command

from tests.test_properties import call, register, staff_client
from tests.test_rent_management import active_tenancy, configure_schedule

pytestmark = pytest.mark.django_db


def setup(client, org, **kw):
    _, _, _, tenancy = active_tenancy(client, org, **kw)
    return tenancy, configure_schedule(client, org, tenancy, next_date="2027-01-31")


def charges(client, org, tenancy):
    return call(client, "get", f"/api/v1/tenancies/{tenancy['id']}/rent-charges/", org).json()


def test_command_catches_up_missed_months_posts_and_is_idempotent(capsys):
    c, org = register()
    tenancy, _ = setup(c, org)
    call_command("generate_rent_invoices", "--date", "2027-01-20")  # nothing due yet
    assert charges(c, org, tenancy) == []
    call_command("generate_rent_invoices", "--post", "--date", "2027-03-15")
    got = charges(c, org, tenancy)
    assert len(got) == 2 and all(x["invoice_status"] == "posted" for x in got)  # Jan 31 and Feb 28 periods
    call_command("generate_rent_invoices", "--post", "--date", "2027-03-15")
    assert len(charges(c, org, tenancy)) == 2


def test_api_generates_due_invoices_for_this_company_only():
    c, org = register()
    tenancy, _ = setup(c, org)
    other, other_org = register("second@example.com", "Other Co")
    other_tenancy, _ = setup(other, other_org)
    # the endpoint uses today's date, which is before the 2027 schedules: nothing due
    r = call(c, "post", "/api/v1/rent-schedules/generate-due/", org, {})
    assert r.status_code == 200 and r.json()["generated"] == 0
    assert call(staff_client(org, "Auditor", "aud@example.com"), "post",
                "/api/v1/rent-schedules/generate-due/", org, {}).status_code == 403
    call_command("generate_rent_invoices", "--date", "2027-01-31")
    assert len(charges(c, org, tenancy)) == 1 and len(charges(other, other_org, other_tenancy)) == 1


def test_schedule_past_tenancy_end_is_skipped_not_fatal(capsys):
    from apps.core.tenancy import organization_context
    from apps.properties.services import generate_due_rent_invoices
    c, org = register()
    tenancy, _ = setup(c, org)
    call(c, "patch", f"/api/v1/tenancies/{tenancy['id']}/", org, {"notes": "x"})
    from apps.properties.models import Tenancy
    with organization_context(org):
        Tenancy.objects.filter(pk=tenancy["id"]).update(end_date=date(2027, 3, 15))
        result = generate_due_rent_invoices(date(2027, 6, 1))
    assert result["generated"] == 1 and len(result["skipped"]) == 1
