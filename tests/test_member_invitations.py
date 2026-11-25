from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.access.models import Invitation, Role, RoleAssignment
from apps.access.services import create_default_roles
from apps.accounts.models import User
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership, Organization

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-pass-123"


def post(client, url, data=None, org=None):
    extra = {"HTTP_X_ORGANIZATION_ID": str(org)} if org else {}
    return client.post(url, data or {}, content_type="application/json", **extra)


def register_owner(email="owner@example.com", company="Acme Homes"):
    client = Client()
    response = post(
        client,
        "/api/v1/register/",
        {"email": email, "password": PASSWORD, "full_name": "Owner", "company_name": company},
    )
    assert response.status_code == 201
    return client, response.json()["organization"]["id"]


def role_id(org_id, name):
    with organization_context(org_id):
        return str(Role.objects.get(name=name).id)


def invite(client, org_id, email, role_name="Employee"):
    return post(
        client,
        "/api/v1/invitations/",
        {"email": email, "role": role_id(org_id, role_name)},
        org=org_id,
    )


def accept(token, password=PASSWORD, full_name="New Person"):
    return post(
        Client(),
        "/api/v1/invitations/accept/",
        {"token": token, "password": password, "full_name": full_name},
    )


def test_administrator_invites_and_new_person_joins_with_the_role():
    owner, org_id = register_owner()
    created = invite(owner, org_id, "Staff@Example.com", "Property officer")
    assert created.status_code == 201
    token = created.json()["token"]

    joined = accept(token)
    assert joined.status_code == 201
    user = User.objects.get(email="staff@example.com")
    assert user.memberships.get().status == Membership.Status.ACTIVE
    with organization_context(org_id):
        assert RoleAssignment.objects.get(user=user).role.name == "Property officer"
        assert Invitation.objects.get().status == Invitation.Status.ACCEPTED

    # The new person can now work in the company with that role's permissions.
    staff = Client()
    assert post(staff, "/api/v1/login/", {"email": "staff@example.com", "password": PASSWORD}).status_code == 200
    me = staff.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=org_id).json()
    assert "properties.payment.record" in me["permissions"]
    assert "access.role.manage" not in me["permissions"]


def test_token_is_single_use_and_hash_only():
    owner, org_id = register_owner()
    token = invite(owner, org_id, "staff@example.com").json()["token"]
    assert accept(token).status_code == 201
    assert accept(token).status_code == 400
    with organization_context(org_id):
        stored = Invitation.objects.get()
    assert token.split(".", 1)[1] not in stored.token_hash
    assert len(stored.token_hash) == 64


def test_forged_unknown_and_malformed_tokens_are_rejected():
    owner, org_id = register_owner()
    real = invite(owner, org_id, "staff@example.com").json()["token"]
    secret = real.split(".", 1)[1]
    other_org = Organization.objects.create(name="Other")
    assert accept(f"{other_org.id}.{secret}").status_code == 400  # right secret, wrong company
    assert accept(f"{org_id}.not-the-secret").status_code == 400
    assert accept("garbage").status_code == 400
    assert accept("").status_code == 400


def test_expired_invitation_is_rejected():
    owner, org_id = register_owner()
    token = invite(owner, org_id, "staff@example.com").json()["token"]
    with organization_context(org_id):
        Invitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert accept(token).status_code == 400
    with organization_context(org_id):
        assert Invitation.objects.get().status == Invitation.Status.EXPIRED
    assert not User.objects.filter(email="staff@example.com").exists()


def test_weak_password_does_not_create_an_account():
    owner, org_id = register_owner()
    token = invite(owner, org_id, "staff@example.com").json()["token"]
    assert accept(token, password="123").status_code == 400
    assert not User.objects.filter(email="staff@example.com").exists()
    assert accept(token).status_code == 201  # the invitation is still usable


def test_existing_account_must_prove_its_password():
    owner, org_id = register_owner()
    other_client, other_org = register_owner("second@example.com", "Second Co")
    token = invite(owner, org_id, "second@example.com", "Auditor").json()["token"]
    assert accept(token, password="wrong-password").status_code == 400
    assert accept(token, password=PASSWORD).status_code == 201
    user = User.objects.get(email="second@example.com")
    assert user.memberships.filter(status="active").count() == 2


def test_user_without_invite_permission_is_refused(org_a):
    create_default_roles(org_a)
    employee = User.objects.create_user(email="emp@example.com", password=PASSWORD)
    Membership.objects.create(user=employee, organization=org_a, status="active")
    with organization_context(org_a.id):
        RoleAssignment.objects.create(user=employee, role=Role.objects.get(name="Employee"))
    client = Client()
    client.force_login(employee)
    response = post(
        client,
        "/api/v1/invitations/",
        {"email": "x@example.com", "role": role_id(org_a.id, "Employee")},
        org=org_a.id,
    )
    assert response.status_code == 403


def test_inviter_cannot_grant_more_access_than_they_have():
    owner, org_id = register_owner()
    with organization_context(org_id):
        limited = Role.objects.create(
            name="Invite only", permissions=[
                "access.user.invite",
                "properties.property.view",
                "contacts.contact.view",
            ]
        )
    token = invite(owner, org_id, "lead@example.com", "Employee").json()["token"]
    # give the new person the limited role by hand, then try to escalate
    assert accept(token).status_code == 201
    lead = User.objects.get(email="lead@example.com")
    with organization_context(org_id):
        RoleAssignment.objects.filter(user=lead).update(role=limited)

    client = Client()
    post(client, "/api/v1/login/", {"email": "lead@example.com", "password": PASSWORD})
    too_much = invite(client, org_id, "boss@example.com", "Administrator")
    assert too_much.status_code == 403
    allowed = post(
        client,
        "/api/v1/invitations/",
        {"email": "ok@example.com", "role": role_id(org_id, "Employee")},
        org=org_id,
    )
    assert allowed.status_code == 201


def test_role_must_belong_to_the_active_company():
    owner, org_id = register_owner()
    _, other_org = register_owner("second@example.com", "Second Co")
    response = post(
        owner,
        "/api/v1/invitations/",
        {"email": "staff@example.com", "role": role_id(other_org, "Administrator")},
        org=org_id,
    )
    assert response.status_code == 400


def test_duplicate_pending_and_existing_member_are_rejected():
    owner, org_id = register_owner()
    assert invite(owner, org_id, "staff@example.com").status_code == 201
    assert invite(owner, org_id, "staff@example.com").status_code == 400
    assert invite(owner, org_id, "owner@example.com").status_code == 400


def test_invitations_stay_inside_their_company():
    owner_a, org_a_id = register_owner()
    owner_b, org_b_id = register_owner("second@example.com", "Second Co")
    invite(owner_a, org_a_id, "staff@example.com")
    assert owner_b.get("/api/v1/invitations/", HTTP_X_ORGANIZATION_ID=org_b_id).json() == []
    # naming a company you do not belong to is refused outright
    assert owner_b.get("/api/v1/invitations/", HTTP_X_ORGANIZATION_ID=org_a_id).status_code == 403
    # and the staff-invitation list does not leak into the accountant list
    listed = owner_a.get("/api/v1/accountant-invitations/list/", HTTP_X_ORGANIZATION_ID=org_a_id)
    assert listed.json() == []


def test_revoked_invitation_cannot_be_accepted():
    owner, org_id = register_owner()
    created = invite(owner, org_id, "staff@example.com").json()
    revoked = post(owner, f"/api/v1/invitations/{created['invitation_id']}/revoke/", org=org_id)
    assert revoked.status_code == 200
    assert accept(created["token"]).status_code == 400


def test_permissions_follow_the_active_company_for_a_user_in_two_companies():
    owner_a, org_a_id = register_owner()
    _, org_b_id = register_owner("second@example.com", "Second Co")
    token = invite(owner_a, org_a_id, "second@example.com", "Employee").json()["token"]
    assert accept(token).status_code == 201

    client = Client()
    post(client, "/api/v1/login/", {"email": "second@example.com", "password": PASSWORD})
    in_b = client.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=org_b_id).json()["permissions"]
    in_a = client.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=org_a_id).json()["permissions"]
    assert "*" in in_b          # Administrator of their own company
    assert in_a == [
        "contacts.contact.view",
        "properties.property.view",
    ]  # Employee permissions in the other company
