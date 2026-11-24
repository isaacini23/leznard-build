from django.db import transaction

from apps.access.models import Role, RoleAssignment
from apps.access.services import create_default_roles
from apps.accounts.models import User
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership, Organization


@transaction.atomic
def register_company(*, email, password, full_name, company_name, country="NG", base_currency="NGN"):
    user = User.objects.create_user(email=email, password=password, full_name=full_name)
    organization = Organization.objects.create(
        name=company_name, country=country, base_currency=base_currency
    )
    Membership.objects.create(user=user, organization=organization)
    create_default_roles(organization)
    with organization_context(organization.id):
        administrator = Role.objects.get(name="Administrator")
        RoleAssignment.objects.create(user=user, role=administrator)
    return user, organization


@transaction.atomic
def create_company_for_user(*, user, company_name, country="NG", base_currency="NGN"):
    """Create another company for an existing authenticated Leznard user."""
    organization = Organization.objects.create(
        name=company_name,
        country=country,
        base_currency=base_currency,
    )
    Membership.objects.create(
        user=user,
        organization=organization,
        status=Membership.Status.ACTIVE,
    )
    create_default_roles(organization)
    with organization_context(organization.id):
        administrator = Role.objects.get(name="Administrator")
        RoleAssignment.objects.create(user=user, role=administrator)
    return organization
