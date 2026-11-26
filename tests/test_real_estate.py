from django.core.files.uploadedfile import SimpleUploadedFile

from tests.test_properties import call, make_contact, make_property, register, staff_client

import pytest

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.4\n" + b"x" * 100


def prop_and_unit(client, org, name="Palm Court", label="A1"):
    prop = make_property(client, org, name).json()
    unit = call(client, "post", f"/api/v1/properties/{prop['id']}/units/", org,
                {"label": label, "unit_type": "flat", "rent_amount": "1000000"}).json()
    return prop, unit


def test_buildings_and_unit_details_and_availability_filter():
    c, org = register()
    prop = make_property(c, org).json()
    b = call(c, "post", f"/api/v1/properties/{prop['id']}/buildings/", org, {"name": "Block A", "floors": 4})
    assert b.status_code == 201, b.content
    assert call(c, "post", f"/api/v1/properties/{prop['id']}/buildings/", org, {"name": "Block A"}).status_code == 400
    url = f"/api/v1/properties/{prop['id']}/units/"
    u1 = call(c, "post", url, org, {"label": "A1", "unit_type": "flat", "building": b.json()["id"],
                                      "floor": 1, "bedrooms": 2, "bathrooms": 2, "area_sqm": "85.50"})
    assert u1.status_code == 201, u1.content
    u2 = call(c, "post", url, org, {"label": "A2", "unit_type": "flat"}).json()
    call(c, "patch", f"/api/v1/units/{u2['id']}/", org, {"occupancy_status": "maintenance"})
    vacant = call(c, "get", "/api/v1/units/?available=1", org).json()
    assert [u["label"] for u in vacant] == ["A1"]
    assert len(call(c, "get", f"/api/v1/units/?building={b.json()['id']}", org).json()) == 1
    # a building from another property is refused
    other = make_property(c, org, "Other").json()
    ou = call(c, "post", f"/api/v1/properties/{other['id']}/units/", org,
              {"label": "X", "unit_type": "flat", "building": b.json()["id"]})
    assert ou.status_code == 400


def test_ownership_shares_cannot_exceed_100_and_roles_are_checked():
    c, org = register()
    prop = make_property(c, org).json()
    tenant = make_contact(c, org, "Ada", "tenant")
    o1 = make_contact(c, org, "Chief Bello", "property_owner")
    o2 = make_contact(c, org, "Mrs Eze", "landlord")
    url = f"/api/v1/properties/{prop['id']}/ownerships/"
    assert call(c, "post", url, org, {"owner": tenant["id"], "share_percent": "100"}).status_code == 400
    first = call(c, "post", url, org, {"owner": o1["id"], "share_percent": "60"})
    assert first.status_code == 201, first.content
    assert call(c, "post", url, org, {"owner": o2["id"], "share_percent": "50"}).status_code == 400
    assert call(c, "post", url, org, {"owner": o2["id"], "share_percent": "40"}).status_code == 201
    ended = call(c, "patch", f"/api/v1/ownerships/{first.json()['id']}/", org, {"end_date": "2026-06-30"})
    assert ended.status_code == 200 and ended.json()["is_current"] is False
    cur = call(c, "get", f"/api/v1/properties/{prop['id']}/", org).json()["current_owners"]
    assert [o["name"] for o in cur] == ["Mrs Eze"]
    other, other_org = register("second@example.com", "Other Co")
    p2 = make_property(other, other_org).json()
    assert call(other, "post", f"/api/v1/properties/{p2['id']}/ownerships/", other_org,
                {"owner": o1["id"], "share_percent": "10"}).status_code == 400


def test_management_record():
    c, org = register()
    prop = make_property(c, org).json()
    mgr = make_contact(c, org, "Prime Agents", "other")
    url = f"/api/v1/properties/{prop['id']}/management/"
    r = call(c, "post", url, org, {"manager": mgr["id"], "fee_percent": "10", "start_date": "2026-01-01"})
    assert r.status_code == 201, r.content
    assert call(c, "post", url, org, {"manager": mgr["id"], "fee_percent": "120", "start_date": "2026-01-01"}).status_code == 400
    assert len(call(c, "get", url, org).json()) == 1


def test_tenancy_lifecycle_updates_unit_occupancy():
    c, org = register()
    _, unit = prop_and_unit(c, org)
    ada = make_contact(c, org, "Ada", "tenant")
    bola = make_contact(c, org, "Bola", "tenant")
    body = {"unit": unit["id"], "tenant": ada["id"], "start_date": "2026-01-01", "rent_amount": "1200000"}
    t = call(c, "post", "/api/v1/tenancies/", org, body)
    assert t.status_code == 201, t.content
    assert t.json()["status"] == "draft"
    act = call(c, "post", f"/api/v1/tenancies/{t.json()['id']}/activate/", org)
    assert act.status_code == 200 and act.json()["status"] == "active"
    assert call(c, "get", f"/api/v1/units/{unit['id']}/", org).json()["occupancy_status"] == "occupied"
    # An active tenancy must not be made vacant or put under maintenance manually.
    for occupancy in ("vacant", "maintenance"):
        changed = call(c, "patch", f"/api/v1/units/{unit['id']}/", org, {"occupancy_status": occupancy})
        assert changed.status_code == 400, changed.content
    # second active tenancy on the same unit is refused
    t2 = call(c, "post", "/api/v1/tenancies/", org, {**body, "tenant": bola["id"]}).json()
    assert call(c, "post", f"/api/v1/tenancies/{t2['id']}/activate/", org).status_code == 400
    # terms are locked after start
    assert call(c, "patch", f"/api/v1/tenancies/{t.json()['id']}/", org, {"rent_amount": "1"}).status_code == 400
    assert call(c, "patch", f"/api/v1/tenancies/{t.json()['id']}/", org, {"notes": "ok"}).status_code == 200
    end = call(c, "post", f"/api/v1/tenancies/{t.json()['id']}/end/", org, {"end_date": "2026-11-30", "reason": "Left"})
    assert end.status_code == 200 and end.json()["status"] == "ended"
    after = call(c, "get", f"/api/v1/units/{unit['id']}/", org).json()
    assert after["occupancy_status"] == "vacant" and after["available_from"] == "2026-11-30"
    assert call(c, "get", f"/api/v1/tenancies/?status=active", org).json() == []


def test_tenancy_requires_tenant_role_and_same_company_unit():
    c, org = register()
    _, unit = prop_and_unit(c, org)
    owner = make_contact(c, org, "Chief", "property_owner")
    r = call(c, "post", "/api/v1/tenancies/", org,
             {"unit": unit["id"], "tenant": owner["id"], "start_date": "2026-01-01", "rent_amount": "1"})
    assert r.status_code == 400
    other, other_org = register("second@example.com", "Other Co")
    t = make_contact(other, other_org, "Zed", "tenant")
    r = call(other, "post", "/api/v1/tenancies/", other_org,
             {"unit": unit["id"], "tenant": t["id"], "start_date": "2026-01-01", "rent_amount": "1"})
    assert r.status_code == 400


def test_cannot_activate_on_unit_under_maintenance():
    c, org = register()
    _, unit = prop_and_unit(c, org)
    call(c, "patch", f"/api/v1/units/{unit['id']}/", org, {"occupancy_status": "maintenance"})
    ada = make_contact(c, org, "Ada", "tenant")
    t = call(c, "post", "/api/v1/tenancies/", org,
             {"unit": unit["id"], "tenant": ada["id"], "start_date": "2026-01-01", "rent_amount": "1"}).json()
    assert call(c, "post", f"/api/v1/tenancies/{t['id']}/activate/", org).status_code == 400


def test_tenant_profile_masks_id_number():
    c, org = register()
    ada = make_contact(c, org, "Ada", "tenant")
    other = make_contact(c, org, "Chief", "property_owner")
    url = f"/api/v1/contacts/{ada['id']}/tenant-profile/"
    assert call(c, "get", url, org).status_code == 404
    r = call(c, "put", url, org, {"occupation": "Engineer", "id_type": "nin", "id_number": "12345678901"})
    assert r.status_code == 201, r.content
    body = r.json()
    assert "id_number" not in body and body["id_number_masked"].endswith("8901")
    assert "12345678901" not in call(c, "get", url, org).content.decode()
    assert call(c, "put", url, org, {"occupation": "Doctor"}).status_code == 200
    assert call(c, "put", f"/api/v1/contacts/{other['id']}/tenant-profile/", org, {"occupation": "x"}).status_code == 400


def upload(client, org, url, name="deed.pdf", data=PDF, **fields):
    payload = {"file": SimpleUploadedFile(name, data), "title": "Title deed", "category": "title_deed", **fields}
    return client.post(url, payload, HTTP_X_ORGANIZATION_ID=org)


def test_document_upload_validation_and_secure_download(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    c, org = register()
    prop, _ = prop_and_unit(c, org)
    url = f"/api/v1/properties/{prop['id']}/documents/"
    ok = upload(c, org, url)
    assert ok.status_code == 201, ok.content
    doc = ok.json()
    assert doc["size_bytes"] == len(PDF) and len(doc["sha256"]) == 64
    assert upload(c, org, url, name="x.exe", data=b"MZ" + b"0" * 50).status_code == 400
    assert upload(c, org, url, name="fake.pdf", data=b"not a pdf at all").status_code == 400
    assert upload(c, org, url, name="big.pdf", data=b"%PDF-" + b"0" * (10 * 1024 * 1024 + 1)).status_code == 400
    dl = c.get(f"/api/v1/documents/{doc['id']}/download/", HTTP_X_ORGANIZATION_ID=org)
    assert dl.status_code == 200
    assert b"".join(dl.streaming_content) == PDF
    assert "attachment" in dl["Content-Disposition"] and dl["X-Content-Type-Options"] == "nosniff"
    # archive instead of delete
    assert call(c, "patch", f"/api/v1/documents/{doc['id']}/", org, {"is_archived": True}).status_code == 200
    assert call(c, "get", url, org).json() == []
    assert len(call(c, "get", url + "?include_archived=1", org).json()) == 1
    # another company cannot download it
    other, other_org = register("second@example.com", "Other Co")
    assert other.get(f"/api/v1/documents/{doc['id']}/download/", HTTP_X_ORGANIZATION_ID=other_org).status_code == 404


def test_role_permissions_for_leases_and_documents():
    c, org = register()
    prop, unit = prop_and_unit(c, org)
    ada = make_contact(c, org, "Ada", "tenant")
    auditor = staff_client(org, "Auditor", "aud@example.com")
    assert call(auditor, "get", "/api/v1/tenancies/", org).status_code == 200
    assert call(auditor, "post", "/api/v1/tenancies/", org,
                {"unit": unit["id"], "tenant": ada["id"], "start_date": "2026-01-01", "rent_amount": "1"}).status_code == 403
    assert call(auditor, "get", f"/api/v1/properties/{prop['id']}/documents/", org).status_code == 200
    assert upload(auditor, org, f"/api/v1/properties/{prop['id']}/documents/").status_code == 403
    officer = staff_client(org, "Property officer", "po@example.com")
    assert call(officer, "post", "/api/v1/tenancies/", org,
                {"unit": unit["id"], "tenant": ada["id"], "start_date": "2026-01-01", "rent_amount": "1"}).status_code == 201


def test_nothing_can_be_deleted():
    c, org = register()
    prop, unit = prop_and_unit(c, org)
    for url in (f"/api/v1/properties/{prop['id']}/", f"/api/v1/units/{unit['id']}/"):
        assert call(c, "delete", url, org).status_code == 405


def test_tenancy_activation_requires_active_property_and_vacant_unit():
    c, org = register()
    ada = make_contact(c, org, "Ada", "tenant")

    for status in ("inactive", "sold"):
        prop = make_property(c, org, f"{status.title()} Estate", status=status).json()
        unit = call(c, "post", f"/api/v1/properties/{prop['id']}/units/", org,
                    {"label": "A1", "unit_type": "flat"}).json()
        tenancy = call(c, "post", "/api/v1/tenancies/", org,
                       {"unit": unit["id"], "tenant": ada["id"], "start_date": "2026-01-01",
                        "rent_amount": "100"}).json()
        response = call(c, "post", f"/api/v1/tenancies/{tenancy['id']}/activate/", org)
        assert response.status_code == 400, response.content

    active_property, unit = prop_and_unit(c, org, "Already Occupied")
    changed = call(c, "patch", f"/api/v1/units/{unit['id']}/", org,
                   {"occupancy_status": "occupied"})
    assert changed.status_code == 200, changed.content
    tenancy = call(c, "post", "/api/v1/tenancies/", org,
                   {"unit": unit["id"], "tenant": ada["id"], "start_date": "2026-01-01",
                    "rent_amount": "100"}).json()
    response = call(c, "post", f"/api/v1/tenancies/{tenancy['id']}/activate/", org)
    assert response.status_code == 400, response.content
