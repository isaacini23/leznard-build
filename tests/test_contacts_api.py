import pytest
from rest_framework.test import APIClient

from apps.access.models import Role, RoleAssignment
from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership, Organization

pytestmark = pytest.mark.django_db


def register_company(client, email, company_name):
    response = client.post(
        "/api/v1/register/",
        {
            "email": email,
            "password": "Str0ng-pass-123",
            "full_name": "Test Owner",
            "company_name": company_name,
        },
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    user = User.objects.get(email=email)
    organization = user.memberships.get().organization
    return user, organization


def org_headers(organization):
    return {"HTTP_X_ORGANIZATION_ID": str(organization.id)}


def test_create_and_retrieve_individual_and_business_contacts(client):
    _, organization = register_company(
        client, "contacts-owner@example.com", "Contacts Company"
    )
    headers = org_headers(organization)

    individual = client.post(
        "/api/v1/contacts/",
        {
            "contact_type": "individual",
            "display_name": "Ada Lovelace",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
        },
        content_type="application/json",
        **headers,
    )
    assert individual.status_code == 201, individual.content
    assert individual.json()["display_name"] == "Ada Lovelace"
    assert individual.json()["roles"] == []
    assert individual.json()["addresses"] == []

    business = client.post(
        "/api/v1/contacts/",
        {
            "contact_type": "organization",
            "display_name": "Northstar Construction",
            "legal_name": "Northstar Construction Limited",
            "tax_id": "TIN-123",
        },
        content_type="application/json",
        **headers,
    )
    assert business.status_code == 201, business.content
    assert business.json()["legal_name"] == "Northstar Construction Limited"

    detail = client.get(
        f"/api/v1/contacts/{individual.json()['id']}/",
        **headers,
    )
    assert detail.status_code == 200
    assert detail.json()["email"] == "ada@example.com"


def test_contact_update_and_soft_deactivation_preserve_history(client):
    _, organization = register_company(
        client, "contact-history@example.com", "History Company"
    )
    headers = org_headers(organization)
    created = client.post(
        "/api/v1/contacts/",
        {"display_name": "Legacy Supplier"},
        content_type="application/json",
        **headers,
    )
    contact_id = created.json()["id"]

    role = client.post(
        f"/api/v1/contacts/{contact_id}/roles/",
        {"role": "supplier"},
        content_type="application/json",
        **headers,
    )
    assert role.status_code == 201

    address = client.post(
        f"/api/v1/contacts/{contact_id}/addresses/",
        {
            "address_type": "office",
            "street_address": "8 Old Market Road",
            "city": "Uyo",
            "state": "Akwa Ibom",
        },
        content_type="application/json",
        **headers,
    )
    assert address.status_code == 201

    updated = client.patch(
        f"/api/v1/contacts/{contact_id}/",
        {"phone": "+2348000000000"},
        content_type="application/json",
        **headers,
    )
    assert updated.status_code == 200
    assert updated.json()["phone"] == "+2348000000000"

    deleted = client.delete(f"/api/v1/contacts/{contact_id}/", **headers)
    assert deleted.status_code == 204

    inactive = client.get(f"/api/v1/contacts/{contact_id}/", **headers)
    assert inactive.status_code == 200
    assert inactive.json()["is_active"] is False
    assert len(inactive.json()["roles"]) == 1
    assert len(inactive.json()["addresses"]) == 1


def test_contact_search_filters_and_pagination(client):
    _, organization = register_company(
        client, "contact-search@example.com", "Search Company"
    )
    headers = org_headers(organization)
    for number in range(1, 5):
        response = client.post(
            "/api/v1/contacts/",
            {"display_name": f"Customer {number:02d}"},
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201

    page = client.get(
        "/api/v1/contacts/?search=Customer&page_size=2&page=1",
        **headers,
    )
    assert page.status_code == 200
    assert page.json()["count"] == 4
    assert len(page.json()["results"]) == 2
    assert page.json()["next"] is not None

    invalid = client.get("/api/v1/contacts/?is_active=perhaps", **headers)
    assert invalid.status_code == 400


def test_contact_roles_can_be_added_listed_and_removed(client):
    _, organization = register_company(
        client, "contact-roles@example.com", "Roles Company"
    )
    headers = org_headers(organization)
    created = client.post(
        "/api/v1/contacts/",
        {"display_name": "BuildCo"},
        content_type="application/json",
        **headers,
    )
    contact_id = created.json()["id"]

    for role_name in ("customer", "supplier", "contractor"):
        response = client.post(
            f"/api/v1/contacts/{contact_id}/roles/",
            {"role": role_name},
            content_type="application/json",
            **headers,
        )
        assert response.status_code == 201, response.content

    roles = client.get(f"/api/v1/contacts/{contact_id}/roles/", **headers)
    assert roles.status_code == 200
    assert {item["role"] for item in roles.json()} == {
        "customer", "supplier", "contractor"
    }

    role_id = roles.json()[0]["id"]
    removed = client.delete(
        f"/api/v1/contacts/{contact_id}/roles/{role_id}/",
        **headers,
    )
    assert removed.status_code == 204


def test_contact_addresses_can_be_created_updated_and_removed(client):
    _, organization = register_company(
        client, "contact-addresses@example.com", "Addresses Company"
    )
    headers = org_headers(organization)
    created = client.post(
        "/api/v1/contacts/",
        {"display_name": "Northstar Ltd"},
        content_type="application/json",
        **headers,
    )
    contact_id = created.json()["id"]

    address = client.post(
        f"/api/v1/contacts/{contact_id}/addresses/",
        {
            "address_type": "office",
            "street_address": "12 Marina Road",
            "city": "Lagos",
            "state": "Lagos",
            "is_default": True,
        },
        content_type="application/json",
        **headers,
    )
    assert address.status_code == 201, address.content

    address_id = address.json()["id"]
    updated = client.patch(
        f"/api/v1/contacts/{contact_id}/addresses/{address_id}/",
        {"city": "Ikeja"},
        content_type="application/json",
        **headers,
    )
    assert updated.status_code == 200
    assert updated.json()["city"] == "Ikeja"

    removed = client.delete(
        f"/api/v1/contacts/{contact_id}/addresses/{address_id}/",
        **headers,
    )
    assert removed.status_code == 204


def test_duplicate_roles_and_default_addresses_are_rejected(client):
    _, organization = register_company(
        client, "contact-validation@example.com", "Validation Company"
    )
    headers = org_headers(organization)
    created = client.post(
        "/api/v1/contacts/",
        {"display_name": "Validation Contact"},
        content_type="application/json",
        **headers,
    )
    contact_id = created.json()["id"]

    role_payload = {"role": "customer"}
    assert client.post(
        f"/api/v1/contacts/{contact_id}/roles/",
        role_payload,
        content_type="application/json",
        **headers,
    ).status_code == 201
    duplicate = client.post(
        f"/api/v1/contacts/{contact_id}/roles/",
        role_payload,
        content_type="application/json",
        **headers,
    )
    assert duplicate.status_code == 400

    address_payload = {
        "address_type": "office",
        "street_address": "1 Main Road",
        "city": "Uyo",
        "is_default": True,
    }
    assert client.post(
        f"/api/v1/contacts/{contact_id}/addresses/",
        address_payload,
        content_type="application/json",
        **headers,
    ).status_code == 201
    duplicate_default = client.post(
        f"/api/v1/contacts/{contact_id}/addresses/",
        {**address_payload, "address_type": "billing"},
        content_type="application/json",
        **headers,
    )
    assert duplicate_default.status_code == 400


def test_employee_can_read_but_cannot_manage_contacts(client):
    _, organization = register_company(
        client, "contact-permissions@example.com", "Permissions Company"
    )
    headers = org_headers(organization)

    with organization_context(organization.id):
        employee = User.objects.create_user(
            email="contact-employee@example.com",
            password="Str0ng-pass-123",
        )
        Membership.objects.create(
            user=employee,
            organization=organization,
            status=Membership.Status.ACTIVE,
        )
        employee_role = Role.objects.get(name="Employee")
        RoleAssignment.objects.create(user=employee, role=employee_role)

    created = client.post(
        "/api/v1/contacts/",
        {"display_name": "Read Only Contact"},
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201

    client.force_login(employee)
    listing = client.get("/api/v1/contacts/", **headers)
    assert listing.status_code == 200
    denied = client.post(
        "/api/v1/contacts/",
        {"display_name": "Not Allowed"},
        content_type="application/json",
        **headers,
    )
    assert denied.status_code == 403


def test_contact_data_is_not_visible_to_another_organization(client):
    _, org_a = register_company(client, "tenant-a@example.com", "Tenant A")
    created = client.post(
        "/api/v1/contacts/",
        {"display_name": "Private Tenant A Contact"},
        content_type="application/json",
        **org_headers(org_a),
    )
    contact_id = created.json()["id"]

    other_client = APIClient()
    _, org_b = register_company(other_client, "tenant-b@example.com", "Tenant B")
    response = other_client.get(
        f"/api/v1/contacts/{contact_id}/",
        **org_headers(org_b),
    )
    assert response.status_code == 404


def test_contacts_require_authentication_and_selected_organization(client):
    assert client.get("/api/v1/contacts/").status_code in (401, 403)

    register_company(client, "no-org-header@example.com", "No Header Company")
    response = client.get("/api/v1/contacts/")
    assert response.status_code == 403


def test_admin_contact_list_uses_authenticated_tenant_context(client):
    organization = Organization.objects.create(name="Admin Tenant")
    admin_user = User.objects.create_user(
        email="contacts-admin@example.com",
        password="Str0ng-pass-123",
        is_staff=True,
        is_superuser=True,
    )
    Membership.objects.create(
        user=admin_user,
        organization=organization,
        status=Membership.Status.ACTIVE,
    )
    with organization_context(organization.id):
        Contact.objects.create(display_name="Visible Admin Contact")

    client.force_login(admin_user)
    response = client.get("/admin/contacts/contact/")
    assert response.status_code == 200
    assert b"Visible Admin Contact" in response.content
