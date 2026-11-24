import pytest

pytestmark = pytest.mark.django_db

import pytest

from apps.access.models import Role, RoleAssignment
from apps.access.services import create_default_roles
from apps.core.tenancy import organization_context
from apps.accounts.models import User
from apps.organizations.models import Membership

REGISTER = {
    "email": "owner@example.com",
    "password": "Str0ng-pass-123",
    "full_name": "Owner One",
    "company_name": "Acme Homes",
}


def register(client, **overrides):
    return client.post("/api/v1/register/", {**REGISTER, **overrides}, content_type="application/json")


def test_register_creates_company_roles_and_administrator(client):
    response = register(client)
    assert response.status_code == 201
    user = User.objects.get(email="owner@example.com")
    membership = user.memberships.get()
    org = membership.organization
    assert org.name == "Acme Homes"
    assert org.base_currency == "NGN"
    with organization_context(org.id):
        assert Role.objects.count() == 6
        assert RoleAssignment.objects.get(user=user).role.name == "Administrator"


def test_register_rejects_duplicate_email(client):
    register(client)
    assert register(client).status_code == 400


def test_register_rejects_weak_password(client):
    assert register(client, password="123").status_code == 400


def test_login_success_and_wrong_password(client):
    register(client)
    client.post("/api/v1/logout/")
    ok = client.post(
        "/api/v1/login/",
        {"email": "OWNER@example.com", "password": REGISTER["password"]},
        content_type="application/json",
    )
    assert ok.status_code == 200
    client.post("/api/v1/logout/")
    bad = client.post(
        "/api/v1/login/",
        {"email": "owner@example.com", "password": "wrong"},
        content_type="application/json",
    )
    assert bad.status_code == 401


def test_me_requires_login(client):
    assert client.get("/api/v1/me/").status_code == 403


def test_administrator_can_list_roles(client):
    register(client)
    org_id = str(User.objects.get(email="owner@example.com").memberships.get().organization_id)
    response = client.get("/api/v1/roles/", HTTP_X_ORGANIZATION_ID=org_id)
    assert response.status_code == 200
    assert len(response.json()) == 6


def test_user_without_permission_gets_403(client, org_a):
    with organization_context(org_a.id):
        create_default_roles(org_a)
        employee = User.objects.create_user(email="emp@example.com", password="pass12345!")
        Membership.objects.create(user=employee, organization=org_a, status="active")
        RoleAssignment.objects.create(user=employee, role=Role.objects.get(name="Employee"))
    client.force_login(employee)
    response = client.get("/api/v1/roles/", HTTP_X_ORGANIZATION_ID=str(org_a.id))
    assert response.status_code == 403


def test_cannot_use_a_company_you_do_not_belong_to(client, org_b):
    register(client)
    response = client.get("/api/v1/roles/", HTTP_X_ORGANIZATION_ID=str(org_b.id))
    assert response.status_code == 403