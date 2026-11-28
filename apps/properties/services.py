from calendar import monthrange
from datetime import timedelta
import uuid

from django.core.exceptions import ValidationError
from decimal import Decimal

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


# ---- owner-friendly rent actions ------------------------------------------------


def rent_amount_paid(invoice):
    from django.db.models import Sum

    from apps.payments.models import Payment, PaymentAllocation

    total = PaymentAllocation.unscoped.filter(
        organization_id=invoice.organization_id,
        sales_invoice=invoice,
        payment__status=Payment.Status.POSTED,
    ).aggregate(total=Sum("amount"))["total"]
    return total or Decimal("0")


def post_rent_invoice(charge_id, *, posted_by=None):
    """Post a rent charge's draft invoice to the ledger (no-op error if already posted)."""
    from apps.sales.services import post_sales_invoice

    charge = RentCharge.objects.select_related("invoice").filter(pk=charge_id).first()
    if charge is None:
        raise ValidationError({"charge": "Rent charge not found."})
    if charge.invoice.status == SalesInvoice.Status.POSTED:
        raise ValidationError({"charge": "This rent invoice is already posted."})
    post_sales_invoice(charge.invoice, posted_by=posted_by)
    return RentCharge.objects.select_related("invoice").get(pk=charge_id)


@transaction.atomic
def record_rent_payment(
    charge_id, *, account_id=None, amount=None, payment_date=None,
    method="bank_transfer", reference="", posted_by=None,
):
    """Record a tenant's rent payment in one step: post the invoice if needed, create the
    receipt, allocate it to the invoice and post it. Defaults to paying what is still due."""
    from django.utils import timezone

    from apps.payments.models import Payment, PaymentAllocation
    from apps.payments.services import post_payment
    from apps.sales.services import post_sales_invoice

    charge = RentCharge.objects.select_related("invoice", "tenancy__tenant").filter(pk=charge_id).first()
    if charge is None:
        raise ValidationError({"charge": "Rent charge not found."})
    invoice = charge.invoice
    if invoice.status == SalesInvoice.Status.DRAFT:
        invoice = post_sales_invoice(invoice, posted_by=posted_by)
    elif invoice.status != SalesInvoice.Status.POSTED:
        raise ValidationError({"charge": "This rent invoice cannot take payments."})

    due = invoice.total - rent_amount_paid(invoice)
    if due <= 0:
        raise ValidationError({"amount": "This rent is already fully paid."})
    try:
        amount = due if amount in (None, "") else Decimal(str(amount))
    except Exception:
        raise ValidationError({"amount": "Enter a valid amount."})
    if amount <= 0:
        raise ValidationError({"amount": "Enter an amount above zero."})
    if amount > due:
        raise ValidationError({"amount": f"That is more than the {due:.2f} still owed."})

    org_id = get_current_org_id()
    if account_id:
        account = Account.objects.filter(pk=account_id).first()
        if account is None:
            raise ValidationError({"account": "Account not found."})
    else:
        account = Account.objects.filter(
            account_type__in=(Account.AccountType.BANK, Account.AccountType.CASH), is_active=True
        ).order_by("account_type", "code").first()
        if account is None:
            raise ValidationError({"account": "Add a bank or cash account first."})

    payment = Payment.objects.create(
        payment_type=Payment.Type.CUSTOMER_RECEIPT,
        payment_date=payment_date or timezone.localdate(),
        contact=charge.tenancy.tenant,
        account=account,
        amount=amount,
        currency=invoice.currency,
        exchange_rate=invoice.exchange_rate,
        method=method,
        reference=(reference or f"RENT-{invoice.number}")[:100],
    )
    PaymentAllocation.objects.create(payment=payment, sales_invoice=invoice, amount=amount)
    post_payment(payment, posted_by=posted_by)
    return RentCharge.objects.select_related("invoice").get(pk=charge_id)


MAX_CATCH_UP_PERIODS = 24


def generate_due_rent_invoices(today, *, post=False, posted_by=None):
    """For the active company: create every rent invoice that has fallen due on or before
    `today`, catching up missed periods, and optionally post them. Safe to run repeatedly:
    a period is never invoiced twice. A schedule that can't continue (for example, the next
    period runs past the tenancy end) is skipped with a reason and the rest carry on."""
    generated, posted, skipped = 0, 0, []
    schedules = RentSchedule.objects.filter(is_active=True, next_invoice_date__lte=today)
    for schedule in schedules.select_related("tenancy__tenant", "tenancy__unit"):
        for _ in range(MAX_CATCH_UP_PERIODS):
            current = RentSchedule.objects.filter(pk=schedule.pk).first()
            if current is None or not current.is_active or current.next_invoice_date > today:
                break
            try:
                with transaction.atomic():
                    charge = generate_next_rent_invoice(schedule.pk)
                    if post:
                        post_rent_invoice(charge.pk, posted_by=posted_by)
                # Count only work that committed successfully. If posting raises,
                # the enclosing transaction rolls the generated invoice back too.
                generated += 1
                if post:
                    posted += 1
            except ValidationError as exc:
                detail = getattr(exc, "message_dict", None) or exc.messages
                skipped.append({
                    "tenancy": str(schedule.tenancy_id),
                    "tenant": schedule.tenancy.tenant.display_name,
                    "unit": schedule.tenancy.unit.label,
                    "reason": detail if isinstance(detail, str) else str(detail),
                })
                break
    return {"generated": generated, "posted": posted, "skipped": skipped}
