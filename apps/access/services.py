from apps.core.tenancy import organization_context

from .models import Role, RoleAssignment
from .permissions import ALL, DEFAULT_ROLES


def create_default_roles(organization):
    with organization_context(organization.id):
        for name, description, codes in DEFAULT_ROLES:
            Role.objects.get_or_create(
                name=name,
                defaults={"description": description, "permissions": codes},
            )


def get_permissions(user):
    if not user.is_authenticated:
        return set()
    assignment = RoleAssignment.objects.filter(user=user).select_related("role").first()
    if assignment is None:
        return set()
    return set(assignment.role.permissions)


def has_permission(user, code):
    granted = get_permissions(user)
    return ALL in granted or code in granted