from calendar import monthrange
from datetime import timedelta
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounting.models import Account
from apps.core.tenancy import get_current_org_id, organization_context
from apps.sales.models import SalesInvoice, SalesInvoiceLine

from .models import Property, RentCharge, RentSchedule, Tenancy, Unit


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
    if unit.property.status != Property.Status.ACTIVE:
        raise ValidationError({"unit": "A tenancy cannot start in an inactive or sold property."})
    if unit.occupancy_status == Unit.Occupancy.MAINTENANCE:
        raise ValidationError({"unit": "This unit is under maintenance."})
    if unit.occupancy_status != Unit.Occupancy.VACANT:
        raise ValidationError({"unit": "Only a vacant unit can be assigned a new tenancy."})
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
    RentSchedule.objects.filter(tenancy=tenancy).update(is_active=False)
    return tenancy

 
_FREQUENCY_MONTHS = {
    Tenancy.Frequency.MONTHLY: 1,
    Tenancy.Frequency.QUARTERLY: 3,
    Tenancy.Frequency.BIANNUAL: 6,
    Tenancy.Frequency.ANNUAL: 12,
}


def _add_billing_months(value, months, billing_day):
    month_index = value.year * 12 + (value.month - 1) + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(billing_day, monthrange(year, month)[1])
    from datetime import date
    return date(year, month, day)


@transaction.atomic
def generate_next_rent_invoice(schedule_id):
    """Create one draft sales invoice for the next rent period and advance its schedule."""
    schedule = RentSchedule.objects.select_related(
        "tenancy__tenant", "tenancy__unit__property", "revenue_account", "organization"
    ).get(pk=schedule_id)
    with organization_context(schedule.organization_id):
        schedule = RentSchedule.unscoped.select_for_update().select_related(
            "tenancy__tenant", "tenancy__unit__property", "revenue_account", "organization"
        ).get(pk=schedule_id)
        tenancy = schedule.tenancy
        if not schedule.is_active:
            raise ValidationError({"schedule": "This rent schedule is inactive."})
        if tenancy.status != Tenancy.Status.ACTIVE:
            raise ValidationError({"tenancy": "Rent can only be billed for an active tenancy."})
        if tenancy.unit.property.status != Property.Status.ACTIVE:
            raise ValidationError({"property": "Rent cannot be billed for an inactive or sold property."})
        months = _FREQUENCY_MONTHS.get(tenancy.rent_frequency)
        if months is None:
            raise ValidationError({"rent_frequency": "This rent frequency is not supported."})

        period_start = schedule.next_invoice_date
        next_start = _add_billing_months(period_start, months, schedule.billing_day or period_start.day)
        period_end = next_start - timedelta(days=1)
        if tenancy.end_date and period_end > tenancy.end_date:
            raise ValidationError({
                "next_invoice_date": "The next rent period extends beyond the tenancy end date. Adjust the tenancy or handle the final period separately."
            })
        if RentCharge.objects.filter(tenancy=tenancy, period_start=period_start).exists():
            raise ValidationError({"next_invoice_date": "This rent period has already been invoiced."})
        if tenancy.rent_amount <= 0:
            raise ValidationError({"rent_amount": "Rent must be greater than zero before an invoice can be generated."})
        if schedule.revenue_account.category != Account.Category.INCOME or not schedule.revenue_account.is_active:
            raise ValidationError({"revenue_account": "Choose an active income account."})

        number = f"RENT-{period_start:%Y%m}-{uuid.uuid4().hex[:8].upper()}"
        invoice = SalesInvoice.objects.create(
            customer=tenancy.tenant,
            tenancy=tenancy,
            number=number,
            document_date=period_start,
            due_date=period_start + timedelta(days=schedule.due_days),
            currency=schedule.organization.base_currency.upper(),
            exchange_rate=1,
            notes=f"Rent for {tenancy.unit.property.name} / {tenancy.unit.label}, "
                  f"{period_start.isoformat()} to {period_end.isoformat()}",
        )
        SalesInvoiceLine.objects.create(
            invoice=invoice,
            description=f"Rent: {tenancy.unit.property.name} / {tenancy.unit.label} "
                        f"({period_start.isoformat()} to {period_end.isoformat()})",
            quantity=1,
            unit_price=tenancy.rent_amount,
            revenue_account=schedule.revenue_account,
        )
        charge = RentCharge.objects.create(
            tenancy=tenancy,
            invoice=invoice,
            period_start=period_start,
            period_end=period_end,
        )
        schedule.next_invoice_date = next_start
        schedule.save(update_fields=["next_invoice_date", "updated_at"])
        return charge
