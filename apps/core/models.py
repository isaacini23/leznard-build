from django.db import models

# Create your models here.
import uuid

from django.core.exceptions import PermissionDenied
from django.db import models

from .tenancy import get_current_org_id


class TenantManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        org_id = get_current_org_id()
        if org_id is None:
            return qs.none()
        return qs.filter(organization_id=org_id)


class TenantModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="+",
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()
    unscoped = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        active = get_current_org_id()
        if self.organization_id is None:
            if active is None:
                raise PermissionDenied("No active organization; refusing to save company data.")
            self.organization_id = active
        elif active is not None and self.organization_id != active:
            raise PermissionDenied("Cannot save a record that belongs to another organization.")
        super().save(*args, **kwargs)