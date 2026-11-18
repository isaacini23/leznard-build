from apps.core.tenancy import organization_context
from apps.organizations.models import Organization
from apps.access.models import Role
from apps.access.services import create_default_roles

alpha = Organization.objects.get(name="Alpha")
beta = Organization.objects.get(name="Beta")

create_default_roles(alpha)
create_default_roles(beta)
create_default_roles(alpha)

with organization_context(alpha.id):
    print("Alpha roles:", list(Role.objects.order_by("name").values_list("name", flat=True)))

with organization_context(beta.id):
    print("Beta roles:", Role.objects.count())

print("No company active:", Role.objects.count())
