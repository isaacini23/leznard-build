from django.db import models

from apps.core.models import TenantModel


class Note(TenantModel):
    text = models.CharField(max_length=200)