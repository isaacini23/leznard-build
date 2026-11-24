import pytest

from apps.access.models import Role, RoleAssignment
from apps.access.services import get_permissions, has_permission
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership


@pytest.mark.django_db
def test_permissions_are_resolved_for_the_active_organization(user_a, org_a, org_b):
    Membership.objects.create(
        user=user_a,
        organization=org_b,
        status=Membership.Status.ACTIVE,
    )

    with organization_context(org_a.id):
        role_a = Role.objects.create(
            name="Alpha accountant",
            permissions=["accounting.journal.view"],
        )
        RoleAssignment.objects.create(user=user_a, role=role_a)

    with organization_context(org_b.id):
        role_b = Role.objects.create(
            name="Beta employee",
            permissions=["properties.property.view"],
        )
        RoleAssignment.objects.create(user=user_a, role=role_b)

    with organization_context(org_a.id):
        assert get_permissions(user_a, org_a.id) == {"accounting.journal.view"}
        assert has_permission(user_a, "accounting.journal.view", org_a.id)
        assert not has_permission(user_a, "properties.property.view", org_a.id)

    with organization_context(org_b.id):
        assert get_permissions(user_a, org_b.id) == {"properties.property.view"}
        assert has_permission(user_a, "properties.property.view", org_b.id)
        assert not has_permission(user_a, "accounting.journal.view", org_b.id)


@pytest.mark.django_db
def test_permission_lookup_fails_closed_without_an_active_organization(user_a, org_a):
    assert get_permissions(user_a, org_a.id) == set()
    assert not has_permission(user_a, "accounting.journal.view", org_a.id)


@pytest.mark.django_db
def test_permission_lookup_rejects_a_different_requested_organization(user_a, org_a, org_b):
    with organization_context(org_a.id):
        role = Role.objects.create(
            name="Alpha administrator",
            permissions=["*"],
        )
        RoleAssignment.objects.create(user=user_a, role=role)

        assert get_permissions(user_a, org_b.id) == set()
        assert not has_permission(user_a, "accounting.journal.view", org_b.id)
