from apps.access.models import Role, RoleAssignment
from apps.access.services import has_permission
from apps.accounts.models import User
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

user = User.objects.first()
alpha = Organization.objects.get(name="Alpha")
beta = Organization.objects.get(name="Beta")

with organization_context(alpha.id):
    accountant = Role.objects.get(name="Accountant")
    RoleAssignment.objects.update_or_create(user=user, defaults={"role": accountant})
    print("Accountant can post:", has_permission(user, "accounting.journal.post"))
    print("Accountant can manage roles:", has_permission(user, "access.role.manage"))

    administrator = Role.objects.get(name="Administrator")
    RoleAssignment.objects.update_or_create(user=user, defaults={"role": administrator})
    print("Administrator can manage roles:", has_permission(user, "access.role.manage"))

with organization_context(beta.id):
    print("No role in Beta:", has_permission(user, "accounting.journal.post"))

print("No company active:", has_permission(user, "accounting.journal.post"))
