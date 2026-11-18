from django.test import Client

from apps.accounts.models import User
from apps.organizations.models import Membership, Organization

user = User.objects.first()
alpha = Organization.objects.get(name="Alpha")
beta = Organization.objects.get(name="Beta")
Membership.objects.get_or_create(user=user, organization=alpha)

client = Client(HTTP_HOST="localhost")

print("not logged in:", client.get("/health/", HTTP_X_ORGANIZATION_ID=str(alpha.id)).status_code)

client.force_login(user)
print("member of Alpha:", client.get("/health/", HTTP_X_ORGANIZATION_ID=str(alpha.id)).status_code)
print("not a member of Beta:", client.get("/health/", HTTP_X_ORGANIZATION_ID=str(beta.id)).status_code)
print("garbage id:", client.get("/health/", HTTP_X_ORGANIZATION_ID="nonsense").status_code)
print("no header:", client.get("/health/").status_code)