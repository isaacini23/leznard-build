from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.tenancy import get_current_org_id

from .models import Tenancy, Unit


@transaction.atomic
def activate_tenancy(tenancy_id):
    """Start a draft tenancy: the unit becomes occupied. One active tenancy per unit."""
    tenancy = Tenancy.objects.select_for_update().filter(pk=tenancy_id).first()
    if tenancy is None:
        raise ValidationError({"tenancy": "Tenancy not found."})
    if tenancy.status != Tenancy.Status.DRAFT:
        raise ValidationError({"tenancy": "Only a draft tenancy can be activated."})
    unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
    if Tenancy.objects.filter(unit=unit, status=Tenancy.Status.ACTIVE).exists():
        raise ValidationError({"unit": "This unit already has an active tenancy."})
    if unit.occupancy_status == Unit.Occupancy.MAINTENANCE:
        raise ValidationError({"unit": "This unit is under maintenance."})
    tenancy.status = Tenancy.Status.ACTIVE
    tenancy.save()
    unit.occupancy_status = Unit.Occupancy.OCCUPIED
    unit.available_from = None
    unit.save()
    return tenancy


@transaction.atomic
def end_tenancy(tenancy_id, *, end_date, reason=""):
    """End an active tenancy: the unit becomes vacant again. The record is kept."""
    tenancy = Tenancy.objects.select_for_update().filter(pk=tenancy_id).first()
    if tenancy is None:
        raise ValidationError({"tenancy": "Tenancy not found."})
    if tenancy.status != Tenancy.Status.ACTIVE:
        raise ValidationError({"tenancy": "Only an active tenancy can be ended."})
    if end_date < tenancy.start_date:
        raise ValidationError({"end_date": "The end date cannot be before the start date."})
    tenancy.status = Tenancy.Status.ENDED
    tenancy.end_date = end_date
    tenancy.end_reason = reason[:300]
    tenancy.save()
    unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
    unit.occupancy_status = Unit.Occupancy.VACANT
    unit.available_from = end_date
    unit.save()
    return tenancy
