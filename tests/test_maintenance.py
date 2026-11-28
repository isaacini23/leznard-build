import pytest

from tests.test_properties import call, make_contact, make_property, register, staff_client

pytestmark = pytest.mark.django_db


def setup(client, org):
    prop = make_property(client, org).json()
    unit = call(client, "post", f"/api/v1/properties/{prop['id']}/units/", org,
                {"label": "A1", "unit_type": "flat"}).json()
    return prop, unit


def test_full_lifecycle_with_contractor_and_cost():
    c, org = register()
    prop, unit = setup(c, org)
    tenant = make_contact(c, org, "Ada", "tenant")
    plumber = make_contact(c, org, "Quick Plumbers", "contractor")
    r = call(c, "post", "/api/v1/maintenance/", org, {
        "property": prop["id"], "unit": unit["id"], "reported_by": tenant["id"],
        "title": "Leaking tap", "category": "plumbing", "priority": "high",
        "estimated_cost": "15000", "assigned_to": plumber["id"],
    })
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "open" and body["reported_date"] and body["unit_label"] == "A1"
    mid = body["id"]
    started = call(c, "post", f"/api/v1/maintenance/{mid}/start/", org, {"scheduled_date": "2027-02-01"})
    assert started.json()["status"] == "in_progress" and started.json()["scheduled_date"] == "2027-02-01"
    assert call(c, "post", f"/api/v1/maintenance/{mid}/start/", org, {}).status_code == 400
    done = call(c, "post", f"/api/v1/maintenance/{mid}/complete/", org,
                {"completed_date": "2027-02-02", "actual_cost": "18000", "resolution_notes": "Replaced washer"})
    assert done.status_code == 200 and done.json()["status"] == "completed" and done.json()["actual_cost"] == "18000.00"
    assert call(c, "patch", f"/api/v1/maintenance/{mid}/", org, {"title": "x"}).status_code == 400
    assert call(c, "post", f"/api/v1/maintenance/{mid}/cancel/", org, {}).status_code == 400
    assert len(call(c, "get", "/api/v1/maintenance/?status=completed&property=" + prop["id"], org).json()) == 1
    assert call(c, "get", "/api/v1/maintenance/?active=1", org).json() == []


def test_validation_rules():
    c, org = register()
    prop, unit = setup(c, org)
    other_prop = make_property(c, org, "Other").json()
    owner = make_contact(c, org, "Chief", "property_owner")
    base = {"property": other_prop["id"], "title": "Roof"}
    assert call(c, "post", "/api/v1/maintenance/", org, {**base, "unit": unit["id"]}).status_code == 400  # unit not in property
    assert call(c, "post", "/api/v1/maintenance/", org, {**base, "assigned_to": owner["id"]}).status_code == 400  # not a contractor
    assert call(c, "post", "/api/v1/maintenance/", org, {**base, "estimated_cost": "-1"}).status_code == 400
    made = call(c, "post", "/api/v1/maintenance/", org, base).json()
    bad = call(c, "post", f"/api/v1/maintenance/{made['id']}/complete/", org, {"completed_date": "2000-01-01"})
    assert bad.status_code == 400  # before report date
    assert call(c, "post", f"/api/v1/maintenance/{made['id']}/complete/", org, {"actual_cost": "x"}).status_code == 400
    assert call(c, "post", f"/api/v1/maintenance/{made['id']}/complete/", org, {"actual_cost": "NaN"}).status_code == 400
    assert call(c, "post", f"/api/v1/maintenance/{made['id']}/complete/", org, {"actual_cost": "Infinity"}).status_code == 400
    cancelled = call(c, "post", f"/api/v1/maintenance/{made['id']}/cancel/", org, {"reason": "Duplicate"})
    assert cancelled.json()["status"] == "cancelled"


def test_permissions_isolation_and_no_delete():
    c, org = register()
    prop, unit = setup(c, org)
    made = call(c, "post", "/api/v1/maintenance/", org, {"property": prop["id"], "title": "Gate"}).json()
    emp = staff_client(org, "Employee", "emp@example.com")
    assert call(emp, "get", "/api/v1/maintenance/", org).status_code == 200
    assert call(emp, "post", "/api/v1/maintenance/", org, {"property": prop["id"], "title": "x"}).status_code == 403
    officer = staff_client(org, "Property officer", "po@example.com")
    assert call(officer, "post", f"/api/v1/maintenance/{made['id']}/start/", org, {}).status_code == 200
    other, other_org = register("second@example.com", "Other Co")
    assert call(other, "get", f"/api/v1/maintenance/{made['id']}/", other_org).status_code == 404
    assert call(other, "post", "/api/v1/maintenance/", other_org, {"property": prop["id"], "title": "x"}).status_code == 400
    assert call(c, "delete", f"/api/v1/maintenance/{made['id']}/", org).status_code == 405
