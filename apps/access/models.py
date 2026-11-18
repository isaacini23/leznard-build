from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import TenantModel


class Role(TenantModel):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    permissions = ArrayField(models.CharField(max_length=100), default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="role_name_unique_per_org")
        ]

    def __str__(self):
        return self.name


class RoleAssignment(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="one_role_per_user_per_org")
        ]