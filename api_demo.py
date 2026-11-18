from django.test import Client

from apps.access.models import Role, RoleAssignment
from apps.accounts.models import User
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

user = User.objects.first()
alpha = Organization.objects.get(name="Alpha")
header = {"HTTP_X_ORGANIZATION_ID": str(alpha.id)}

client = Client(HTTP_HOST="localhost")
print("not logged in:", client.get("/api/v1/me/", **header).status_code)

client.force_login(user)

with organization_context(alpha.id):
    RoleAssignment.objects.update_or_create(
        user=user, defaults={"role": Role.objects.get(name="Employee")}
    )
r = client.get("/api/v1/me/", **header)
print("me as Employee:", r.status_code, r.json()["permissions"])
print("roles as Employee:", client.get("/api/v1/roles/", **header).status_code)

with organization_context(alpha.id):
    RoleAssignment.objects.update_or_create(
        user=user, defaults={"role": Role.objects.get(name="Administrator")}
    )
r = client.get("/api/v1/roles/", **header)
print("roles as Administrator:", r.status_code, [x["name"] for x in r.json()])
