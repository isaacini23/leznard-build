from apps.core.tenancy import get_current_org_id, organization_context

from .models import Role, RoleAssignment
from .permissions import ALL, DEFAULT_ROLES


def create_default_roles(organization):
    with organization_context(organization.id):
        for name, description, codes in DEFAULT_ROLES:
            Role.objects.get_or_create(
                name=name,
                defaults={"description": description, "permissions": codes},
            )


def get_permissions(user, organization_id=None):
    """Return permissions only for the explicitly active organization."""
    if not user.is_authenticated:
        return set()

    active_org_id = get_current_org_id()
    if active_org_id is None:
        return set()

    requested_org_id = organization_id or active_org_id
    if str(requested_org_id) != str(active_org_id):
        return set()

    assignment = (
        RoleAssignment.objects.filter(
            user=user,
            organization_id=active_org_id,
            role__organization_id=active_org_id,
        )
        .select_related("role")
        .first()
    )
    if assignment is None:
        return set()
    return set(assignment.role.permissions)


def has_permission(user, code, organization_id=None):
    granted = get_permissions(user, organization_id=organization_id)
    return ALL in granted or code in granted
