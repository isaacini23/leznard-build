import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError

from apps.access.models import AccountantAccessGrant, Role, RoleAssignment
from apps.access.services import (
    accept_accountant_invitation,
    create_accountant_invitation,
    create_default_roles,
    get_permissions,
    revoke_accountant_access,
)
from apps.core.tenancy import get_current_org_id, organization_context
from apps.organizations.models import Membership, Organization
from apps.accounts.models import User


@pytest.mark.django_db
def test_accountant_invitation_creates_client_owned_scoped_grant(user_a, org_a, org_b):
    firm_user = user_a
    client_owner = User.objects.create_user(email="client@example.com", password="pass12345!")
    Membership.objects.create(user=client_owner, organization=org_b, status=Membership.Status.ACTIVE)

    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=firm_user, role=Role.objects.get(name="Administrator"))
        invitation, token = create_accountant_invitation(
            invited_by=firm_user,
            email=client_owner.email,
            requested_permissions=["accounting.journal.view", "reports.report.view"],
        )
        assert token.startswith(f"{org_a.id}.")
        assert invitation.token_hash not in token
        assert invitation.requested_permissions == [
            "accounting.journal.view", "reports.report.view"
        ]

    grant = accept_accountant_invitation(
        token=token,
        accepted_by=client_owner,
        client_organization_id=org_b.id,
        approved_permissions=["accounting.journal.view"],
    )
    assert grant.organization_id == org_b.id
    assert grant.accountant_organization_id == org_a.id
    assert grant.permissions == ["accounting.journal.view"]
    assert grant.status == AccountantAccessGrant.Status.ACTIVE

    with organization_context(org_b.id):
        assert get_permissions(firm_user) == {"accounting.journal.view"}
        assert AccountantAccessGrant.objects.filter(id=grant.id).exists()

    with organization_context(org_a.id):
        assert invitation.__class__.objects.get(id=invitation.id).status == "accepted"


@pytest.mark.django_db
def test_invitation_cannot_be_accepted_by_different_email(user_a, org_a, org_b):
    Membership.objects.create(user=user_a, organization=org_b, status=Membership.Status.ACTIVE)
    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        invitation, token = create_accountant_invitation(
            invited_by=user_a,
            email="other@example.com",
            requested_permissions=["reports.report.view"],
        )

    with pytest.raises(PermissionDenied):
        accept_accountant_invitation(
            token=token,
            accepted_by=user_a,
            client_organization_id=org_b.id,
            approved_permissions=["reports.report.view"],
        )


@pytest.mark.django_db
def test_client_cannot_approve_permissions_not_requested(user_a, org_a, org_b):
    Membership.objects.create(user=user_a, organization=org_b, status=Membership.Status.ACTIVE)
    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        _, token = create_accountant_invitation(
            invited_by=user_a,
            email=user_a.email,
            requested_permissions=["reports.report.view"],
        )

    with pytest.raises(ValidationError):
        accept_accountant_invitation(
            token=token,
            accepted_by=user_a,
            client_organization_id=org_b.id,
            approved_permissions=["accounting.journal.post"],
        )


@pytest.mark.django_db
def test_client_owner_can_revoke_accountant_access(user_a, org_a, org_b):
    firm_user = user_a
    client_owner = User.objects.create_user(email="owner-revoke@example.com", password="pass12345!")
    Membership.objects.create(user=client_owner, organization=org_b, status=Membership.Status.ACTIVE)

    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=firm_user, role=Role.objects.get(name="Administrator"))
        _, token = create_accountant_invitation(
            invited_by=firm_user,
            email=client_owner.email,
            requested_permissions=["reports.report.view"],
        )
    grant = accept_accountant_invitation(
        token=token,
        accepted_by=client_owner,
        client_organization_id=org_b.id,
        approved_permissions=["reports.report.view"],
    )
    with organization_context(org_b.id):
        create_default_roles(org_b)
        RoleAssignment.objects.create(user=client_owner, role=Role.objects.get(name="Administrator"))
        assert get_permissions(firm_user) == {"reports.report.view"}
        revoke_accountant_access(grant_id=grant.id, revoked_by=client_owner)
        assert get_permissions(firm_user) == set()
        assert AccountantAccessGrant.objects.get(id=grant.id).status == "revoked"


@pytest.mark.django_db
def test_role_assignment_rejects_suspended_or_nonmember_user(user_a, org_a):
    with organization_context(org_a.id):
        role = Role.objects.create(name="Test", permissions=["reports.report.view"])
        user_a.memberships.update(status=Membership.Status.SUSPENDED)
        with pytest.raises(ValidationError):
            RoleAssignment.objects.create(user=user_a, role=role)


@pytest.mark.django_db
def test_nested_organization_context_restores_outer_context(org_a, org_b):
    from django.db import connection

    with organization_context(org_a.id):
        assert get_current_org_id() == org_a.id
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_org', true)")
            assert cursor.fetchone()[0] == str(org_a.id)
        with organization_context(org_b.id):
            assert get_current_org_id() == org_b.id
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_setting('app.current_org', true)")
                assert cursor.fetchone()[0] == str(org_b.id)
        assert get_current_org_id() == org_a.id
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('app.current_org', true)")
            assert cursor.fetchone()[0] == str(org_a.id)
    assert get_current_org_id() is None


@pytest.mark.django_db
def test_failed_database_setup_resets_context(monkeypatch, org_a):
    from apps.core import tenancy

    def fail(_value):
        raise DatabaseError("simulated database setup failure")

    closed = []
    monkeypatch.setattr(tenancy, "_set_db_setting", fail)
    monkeypatch.setattr(tenancy.connection, "close", lambda: closed.append(True))
    with pytest.raises(DatabaseError):
        with organization_context(org_a.id):
            pass
    assert get_current_org_id() is None
    assert closed


@pytest.mark.django_db
def test_stale_database_tenant_setting_is_cleared(org_a, org_b):
    from django.db import connection
    from apps.core.tenancy import _set_db_setting

    _set_db_setting(str(org_a.id))
    with organization_context(org_b.id):
        assert get_current_org_id() == org_b.id
    assert get_current_org_id() is None
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_org', true)")
        assert cursor.fetchone()[0] in ("", None)


@pytest.mark.django_db
def test_accountant_with_active_grant_can_select_client_company(client, user_a, org_a, org_b):
    from apps.access.services import accept_accountant_invitation, create_accountant_invitation

    firm_user = user_a
    client_owner = User.objects.create_user(email="api-client@example.com", password="pass12345!")
    Membership.objects.create(user=client_owner, organization=org_b, status=Membership.Status.ACTIVE)

    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=firm_user, role=Role.objects.get(name="Administrator"))
        _, token = create_accountant_invitation(
            invited_by=firm_user,
            email=client_owner.email,
            requested_permissions=["reports.report.view"],
        )
    accept_accountant_invitation(
        token=token,
        accepted_by=client_owner,
        client_organization_id=org_b.id,
        approved_permissions=["reports.report.view"],
    )

    client.force_login(firm_user)
    response = client.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=str(org_b.id))
    assert response.status_code == 200
    assert response.json()["active_organization"] == str(org_b.id)
    assert response.json()["permissions"] == ["reports.report.view"]


@pytest.mark.django_db
def test_expired_accountant_invitation_is_marked_expired(user_a, org_a, org_b):
    from django.utils import timezone
    from datetime import timedelta

    Membership.objects.create(user=user_a, organization=org_b, status=Membership.Status.ACTIVE)
    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        invitation, token = create_accountant_invitation(
            invited_by=user_a,
            email=user_a.email,
            requested_permissions=["reports.report.view"],
        )
        invitation.expires_at = timezone.now() - timedelta(minutes=1)
        invitation.save()

    with pytest.raises(ValidationError):
        accept_accountant_invitation(
            token=token,
            accepted_by=user_a,
            client_organization_id=org_b.id,
            approved_permissions=["reports.report.view"],
        )

    with organization_context(org_a.id):
        assert invitation.__class__.objects.get(id=invitation.id).status == "expired"



@pytest.mark.django_db
def test_firm_can_revoke_pending_invitation(user_a, org_a):
    from apps.access.services import revoke_accountant_invitation

    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        invitation, token = create_accountant_invitation(
            invited_by=user_a,
            email="pending-client@example.com",
            requested_permissions=["reports.report.view"],
        )
        revoked = revoke_accountant_invitation(
            invitation_id=invitation.id,
            revoked_by=user_a,
        )
        assert revoked.status == "revoked"

        with pytest.raises(ValidationError):
            accept_accountant_invitation(
                token=token,
                accepted_by=user_a,
                client_organization_id=org_a.id,
                approved_permissions=["reports.report.view"],
            )


@pytest.mark.django_db
def test_duplicate_pending_accountant_invitation_is_rejected(user_a, org_a):
    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        create_accountant_invitation(
            invited_by=user_a,
            email="duplicate@example.com",
            requested_permissions=["reports.report.view"],
        )
        with pytest.raises(ValidationError):
            create_accountant_invitation(
                invited_by=user_a,
                email="duplicate@example.com",
                requested_permissions=["reports.report.view"],
            )


@pytest.mark.django_db
def test_suspended_member_loses_role_permissions(user_a, org_a):
    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        user_a.memberships.update(status=Membership.Status.SUSPENDED)
        assert get_permissions(user_a) == set()


@pytest.mark.django_db
def test_role_from_another_company_cannot_be_assigned(user_a, org_a, org_b):
    Membership.objects.create(user=user_a, organization=org_b, status=Membership.Status.ACTIVE)
    with organization_context(org_a.id):
        foreign_role = Role.objects.create(name="Foreign", permissions=["*"])
    with organization_context(org_b.id):
        with pytest.raises(ValidationError):
            RoleAssignment.objects.create(user=user_a, role=foreign_role)



@pytest.mark.django_db
def test_firm_member_without_role_cannot_use_client_grant(client, user_a, org_a, org_b):
    from apps.access.services import accept_accountant_invitation, create_accountant_invitation

    client_owner = User.objects.create_user(email="role-client@example.com", password="pass12345!")
    firm_employee = User.objects.create_user(email="unassigned-firm@example.com", password="pass12345!")
    Membership.objects.create(user=client_owner, organization=org_b, status=Membership.Status.ACTIVE)
    Membership.objects.create(user=firm_employee, organization=org_a, status=Membership.Status.ACTIVE)

    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        _, token = create_accountant_invitation(
            invited_by=user_a,
            email=client_owner.email,
            requested_permissions=["reports.report.view"],
        )
    accept_accountant_invitation(
        token=token,
        accepted_by=client_owner,
        client_organization_id=org_b.id,
        approved_permissions=["reports.report.view"],
    )

    client.force_login(firm_employee)
    response = client.get("/api/v1/me/", HTTP_X_ORGANIZATION_ID=str(org_b.id))
    assert response.status_code == 403



@pytest.mark.django_db
def test_existing_user_can_create_company_then_accept_accountant_invitation(
    client, user_a, org_a
):
    client_owner = User.objects.create_user(
        email="existing-owner@example.com",
        password="pass12345!",
    )
    client.force_login(client_owner)
    company_response = client.post(
        "/api/v1/organizations/",
        {"company_name": "Owner's New Business"},
        content_type="application/json",
    )
    assert company_response.status_code == 201
    client_org_id = company_response.json()["organization"]["id"]
    assert client_owner.memberships.filter(
        organization_id=client_org_id,
        status=Membership.Status.ACTIVE,
    ).exists()

    with organization_context(org_a.id):
        create_default_roles(org_a)
        RoleAssignment.objects.create(user=user_a, role=Role.objects.get(name="Administrator"))
        _, token = create_accountant_invitation(
            invited_by=user_a,
            email=client_owner.email,
            requested_permissions=["reports.report.view"],
        )

    grant = accept_accountant_invitation(
        token=token,
        accepted_by=client_owner,
        client_organization_id=client_org_id,
        approved_permissions=["reports.report.view"],
    )
    assert str(grant.organization_id) == client_org_id
    assert grant.permissions == ["reports.report.view"]
