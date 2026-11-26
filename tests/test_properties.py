import json

import pytest
from django.test import Client

from apps.access.models import Role, RoleAssignment
from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership, Organization

pytestmark = pytest.mark.django_db
PASSWORD = "Str0ng-pass-123"


def register(email="owner@example.com", company="Acme Homes"):
    client = Client()
    response = client.post(
        "/api/v1/register/",
        {"email": email, "password": PASSWORD, "full_name": "Owner", "company_name": company},
        content_type="application/json",
    )
    return client, response.json()["organization"]["id"]


def call(client, method, url, org, data=None):
    kwargs = {"HTTP_X_ORGANIZATION_ID": org}
    if data is not None:
        kwargs["data"] = json.dumps(data)
        kwargs["content_type"] = "application/json"
    return getattr(client, method)(url, **kwargs)


def staff_client(org_id, role_name, email):
    org = Organization.objects.get(id=org_id)
    user = User.objects.create_user(email=email, password=PASSWORD)
    Membership.objects.create(user=user, organization=org)
    with organization_context(org.id):
        RoleAssignment.objects.create(user=user, role=Role.objects.get(name=role_name))
    client = Client()
    client.force_login(user)
    return client


def make_contact(client, org, name, *roles):
    made = call(client, "post", "/api/v1/contacts/", org, {"display_name": name})
    assert made.status_code == 201, made.content
    contact = made.json()
    for role in roles:
        added = call(client, "post", f"/api/v1/contacts/{contact['id']}/roles/", org, {"role": role})
        assert added.status_code == 201, added.content
    return contact


def make_property(client, org, name="Palm Court", **extra):
    body = {
        "name": name, "property_type": "residential",
        "address_line": "12 Admiralty Way", "city": "Lekki", "state": "Lagos", **extra,
    }
    return call(client, "post", "/api/v1/properties/", org, body)


def test_create_property_and_units_and_list_them():
    client, org = register()
    created = make_property(client, org)
    assert created.status_code == 201, created.content
    prop = created.json()
    assert prop["country"] == "NG" and prop["status"] == "active" and prop["unit_count"] == 0

    url = f"/api/v1/properties/{prop['id']}/units/"
    flat = call(client, "post", url, org, {"label": "A1", "unit_type": "flat", "rent_amount": "1500000.00"})
    assert flat.status_code == 201, flat.content
    assert flat.json()["occupancy_status"] == "vacant"
    assert call(client, "post", url, org, {"label": "A2", "unit_type": "flat"}).status_code == 201

    units = call(client, "get", url, org).json()
    assert [u["label"] for u in units] == ["A1", "A2"]
    assert call(client, "get", f"/api/v1/properties/{prop['id']}/", org).json()["unit_count"] == 2

    moved = call(client, "patch", f"/api/v1/units/{flat.json()['id']}/", org, {"occupancy_status": "maintenance"})
    assert moved.status_code == 200 and moved.json()["occupancy_status"] == "maintenance"


def test_validation_rules():
    client, org = register()
    prop = make_property(client, org).json()
    assert make_property(client, org).status_code == 400  # duplicate name
    url = f"/api/v1/properties/{prop['id']}/units/"
    assert call(client, "post", url, org, {"label": "A1", "unit_type": "flat"}).status_code == 201
    assert call(client, "post", url, org, {"label": "A1", "unit_type": "flat"}).status_code == 400
    assert call(client, "post", url, org, {"label": "B1", "unit_type": "flat", "rent_amount": "-5"}).status_code == 400
    assert call(client, "post", url, org, {"label": "B2", "unit_type": "spaceship"}).status_code == 400




def test_owner_must_be_an_owner_or_landlord_contact_of_the_same_company():
    client, org = register()
    tenant = make_contact(client, org, "Ada", "tenant")
    owner = make_contact(client, org, "Chief Bello", "property_owner")
    landlord = make_contact(client, org, "Mrs Eze", "landlord")
    assert make_property(client, org, "P1", owner=tenant["id"]).status_code == 400
    assert make_property(client, org, "P2", owner=owner["id"]).status_code == 201
    assert make_property(client, org, "P3", owner=landlord["id"]).status_code == 201

    other, other_org = register("second@example.com", "Other Co")
    assert make_property(other, other_org, "P4", owner=owner["id"]).status_code == 400  # another company's contact


def test_a_company_cannot_see_or_touch_another_companys_records():
    a, org_a = register()
    b, org_b = register("second@example.com", "Other Co")
    prop = make_property(a, org_a).json()
    unit = call(a, "post", f"/api/v1/properties/{prop['id']}/units/", org_a, {"label": "A1", "unit_type": "flat"}).json()

    assert call(b, "get", "/api/v1/properties/", org_b).json() == []
    assert call(b, "get", f"/api/v1/properties/{prop['id']}/", org_b).status_code == 404
    assert call(b, "get", f"/api/v1/units/{unit['id']}/", org_b).status_code == 404
    assert call(b, "patch", f"/api/v1/units/{unit['id']}/", org_b, {"label": "hacked"}).status_code == 404
    assert call(b, "post", f"/api/v1/properties/{prop['id']}/units/", org_b, {"label": "Z", "unit_type": "flat"}).status_code == 404
    assert call(b, "get", "/api/v1/properties/", org_a).status_code == 403  # not a member of A


def test_roles_control_who_can_view_and_change_properties():
    owner, org = register()
    employee = staff_client(org, "Employee", "emp@example.com")
    officer = staff_client(org, "Property officer", "off@example.com")
    auditor = staff_client(org, "Auditor", "aud@example.com")
    make_property(owner, org)

    assert call(employee, "get", "/api/v1/properties/", org).status_code == 200  # may look
    assert make_property(employee, org, "Nope").status_code == 403
    assert make_property(officer, org, "Officer Court").status_code == 201
    assert call(auditor, "get", "/api/v1/properties/", org).status_code == 200
    assert make_property(auditor, org, "Audit Court").status_code == 403
    unit_url = f"/api/v1/properties/{make_property(owner, org, 'Unit Court').json()['id']}/units/"
    assert call(employee, "post", unit_url, org, {"label": "A1", "unit_type": "flat"}).status_code == 403
    assert call(officer, "post", unit_url, org, {"label": "A1", "unit_type": "flat"}).status_code == 201


def test_properties_and_units_are_never_deleted_only_deactivated():
    client, org = register()
    prop = make_property(client, org).json()
    unit = call(client, "post", f"/api/v1/properties/{prop['id']}/units/", org, {"label": "A1", "unit_type": "flat"}).json()
    assert call(client, "delete", f"/api/v1/properties/{prop['id']}/", org).status_code == 405
    assert call(client, "delete", f"/api/v1/units/{unit['id']}/", org).status_code == 405
    sold = call(client, "patch", f"/api/v1/properties/{prop['id']}/", org, {"status": "sold"})
    assert sold.status_code == 200 and sold.json()["status"] == "sold"
    assert call(client, "get", f"/api/v1/units/{unit['id']}/", org).status_code == 200  # history kept
