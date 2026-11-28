from calendar import monthrange
from datetime import timedelta
import uuid

from django.core.exceptions import ValidationError
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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
                    generated += 1
                    if post:
                        post_rent_invoice(charge.pk, posted_by=posted_by)
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


# ---- maintenance ----------------------------------------------------------------


def _maintenance_for_update(request_id):
    from .models import MaintenanceRequest

    obj = MaintenanceRequest.objects.select_for_update().filter(pk=request_id).first()
    if obj is None:
        raise ValidationError({"request": "Maintenance request not found."})
    return obj


@transaction.atomic
def start_maintenance(request_id, *, scheduled_date=None):
    from .models import MaintenanceRequest

    obj = _maintenance_for_update(request_id)
    if obj.status != MaintenanceRequest.Status.OPEN:
        raise ValidationError({"status": "Only an open request can be started."})
    obj.status = MaintenanceRequest.Status.IN_PROGRESS
    if scheduled_date:
        obj.scheduled_date = scheduled_date
    obj.save()
    return obj


@transaction.atomic
def complete_maintenance(request_id, *, completed_date, actual_cost=None, resolution_notes=""):
    from .models import MaintenanceRequest

    obj = _maintenance_for_update(request_id)
    if obj.status not in (MaintenanceRequest.Status.OPEN, MaintenanceRequest.Status.IN_PROGRESS):
        raise ValidationError({"status": "Only an open or in-progress request can be completed."})
    obj.status = MaintenanceRequest.Status.COMPLETED
    obj.completed_date = completed_date
    if actual_cost is not None:
        obj.actual_cost = actual_cost
    obj.resolution_notes = resolution_notes or obj.resolution_notes
    obj.save()
    return obj


@transaction.atomic
def cancel_maintenance(request_id, *, reason=""):
    from .models import MaintenanceRequest

    obj = _maintenance_for_update(request_id)
    if obj.status in (MaintenanceRequest.Status.COMPLETED, MaintenanceRequest.Status.CANCELLED):
        raise ValidationError({"status": "A finished request cannot be cancelled."})
    obj.status = MaintenanceRequest.Status.CANCELLED
    obj.resolution_notes = reason or obj.resolution_notes
    obj.save()
    return obj


@transaction.atomic
def bill_maintenance(request_id, *, bill_date=None, due_days=30, post=False, posted_by=None):
    """Turn a completed job's actual cost into a purchase bill from the contractor, charged to
    Repairs and maintenance and tagged to the property (so it shows in profitability)."""
    from django.utils import timezone

    from apps.contacts.models import ContactRole
    from apps.sales.models import PurchaseBill, PurchaseBillLine
    from apps.sales.services import post_purchase_bill

    from .models import MaintenanceRequest

    obj = _maintenance_for_update(request_id)
    if obj.status != MaintenanceRequest.Status.COMPLETED:
        raise ValidationError({"status": "Only a completed job can be billed."})
    if obj.purchase_bill_id:
        raise ValidationError({"purchase_bill": "This job has already been billed."})
    if not obj.actual_cost or obj.actual_cost <= 0:
        raise ValidationError({"actual_cost": "Record the actual cost before billing."})
    if obj.assigned_to_id is None:
        raise ValidationError({"assigned_to": "Assign a contractor or supplier before billing."})
    if not ContactRole.objects.filter(contact=obj.assigned_to, role=ContactRole.Role.SUPPLIER).exists():
        raise ValidationError({"assigned_to": "Give this contact the Supplier role so the bill can be paid."})
    repairs = Account.objects.filter(
        account_type=Account.AccountType.REPAIRS, is_active=True
    ).order_by("code").first()
    if repairs is None:
        raise ValidationError({"account": "No active Repairs and maintenance account exists."})

    organization = obj.organization
    day = bill_date or timezone.localdate()
    bill = PurchaseBill.objects.create(
        supplier=obj.assigned_to,
        property=obj.property,
        number=f"MNT-{day:%Y%m}-{uuid.uuid4().hex[:8].upper()}",
        document_date=day,
        due_date=day + timedelta(days=due_days),
        currency=organization.base_currency.upper(),
        exchange_rate=1,
        notes=f"Maintenance: {obj.title}",
    )
    PurchaseBillLine.objects.create(
        bill=bill, description=f"{obj.title} ({obj.property.name})", quantity=1,
        unit_price=obj.actual_cost, expense_account=repairs,
    )
    obj.purchase_bill = bill
    obj.save()
    if post:
        post_purchase_bill(bill, posted_by=posted_by)
    return obj


@transaction.atomic
def renew_tenancy(
    tenancy_id, *, start_date, end_date=None, rent_amount=None, rent_frequency=None,
    deposit_amount=None, notes="",
):
    """Renew an active tenancy on new terms (for example a rent increase). The old tenancy is
    closed the day before `start_date` and a new active one begins for the same tenant and unit,
    which stays occupied. Rent billing carries on from where it left off, at the new rent."""
    old = Tenancy.objects.select_for_update().filter(pk=tenancy_id).first()
    if old is None:
        raise ValidationError({"tenancy": "Tenancy not found."})
    if old.status != Tenancy.Status.ACTIVE:
        raise ValidationError({"tenancy": "Only an active tenancy can be renewed."})
    if start_date <= old.start_date:
        raise ValidationError({"start_date": "The renewal must start after the current tenancy started."})
    if end_date and end_date < start_date:
        raise ValidationError({"end_date": "The end date cannot be before the start date."})
    old_schedule = RentSchedule.objects.select_for_update().filter(tenancy=old, is_active=True).first()
    if old_schedule and old_schedule.next_invoice_date < start_date:
        raise ValidationError({
            "start_date": "Invoice the rent periods before the renewal date first, so they are billed at the old rent."
        })

    old.status = Tenancy.Status.ENDED
    old.end_date = start_date - timedelta(days=1)
    old.end_reason = "Renewed"
    old.save()
    new = Tenancy.objects.create(
        unit=old.unit,
        tenant=old.tenant,
        start_date=start_date,
        end_date=end_date,
        rent_amount=old.rent_amount if rent_amount is None else rent_amount,
        rent_frequency=rent_frequency or old.rent_frequency,
        deposit_amount=old.deposit_amount if deposit_amount is None else deposit_amount,
        status=Tenancy.Status.ACTIVE,
        notes=notes or old.notes,
        renewed_from=old,
    )
    if old_schedule:
        old_schedule.is_active = False
        old_schedule.save(update_fields=["is_active", "updated_at"])
        RentSchedule.objects.create(
            tenancy=new,
            revenue_account=old_schedule.revenue_account,
            next_invoice_date=old_schedule.next_invoice_date,
            billing_day=old_schedule.billing_day,
            due_days=old_schedule.due_days,
        )
    return new


# ---- tenant deposits ------------------------------------------------------------

DEPOSIT_ACCOUNT_CODE = "2300"


@transaction.atomic
def _deposit_liability_account():
    account, _ = Account.objects.get_or_create(
        code=DEPOSIT_ACCOUNT_CODE,
        defaults={
            "name": "Tenant Deposits Held",
            "category": Account.Category.LIABILITY,
            "account_type": Account.AccountType.OTHER_LIABILITY,
            "normal_balance": Account.NormalBalance.CREDIT,
            "is_system": True,
        },
    )
    if (
        account.category != Account.Category.LIABILITY
        or account.account_type != Account.AccountType.OTHER_LIABILITY
        or account.normal_balance != Account.NormalBalance.CREDIT
        or not account.is_active
    ):
        raise ValidationError({
            "deposit_account": (
                "Account 2300 must be an active Other Liability account with a credit normal balance."
            )
        })
    return account


def _tenancy_chain(tenancy):
    """The tenancy and the earlier tenancies it renewed: a deposit follows the tenant."""
    chain = [tenancy.pk]
    current = tenancy
    while current.renewed_from_id:
        chain.append(current.renewed_from_id)
        current = Tenancy.objects.get(pk=current.renewed_from_id)
    return chain


def deposit_summary(tenancy):
    from .models import DepositTransaction

    rows = list(DepositTransaction.objects.filter(tenancy_id__in=_tenancy_chain(tenancy)))
    total = lambda kind: sum((r.amount for r in rows if r.kind == kind), Decimal("0"))
    received, deducted, refunded = total("received"), total("deduction"), total("refund")
    return {
        "agreed": tenancy.deposit_amount, "received": received, "deducted": deducted,
        "refunded": refunded, "held": received - deducted - refunded, "transactions": rows,
    }


def _bank_or_cash(account_id):
    if not account_id:
        raise ValidationError({"account": "Select the bank or cash account that received or paid the deposit."})
    account = Account.objects.filter(pk=account_id).first()
    if account is None:
        raise ValidationError({"account": "Account not found in this company."})
    if not account.is_active:
        raise ValidationError({"account": "Choose an active bank or cash account."})
    if account.account_type not in (Account.AccountType.BANK, Account.AccountType.CASH):
        raise ValidationError({"account": "Choose a bank or cash account."})
    return account


def _positive(amount, field="amount"):
    try:
        value = Decimal(str(amount))
        if not value.is_finite():
            raise InvalidOperation
        rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({field: "Enter a valid amount with no more than two decimal places."})
    if rounded <= 0:
        raise ValidationError({field: "Enter an amount of at least 0.01."})
    return rounded


def _post_deposit_movement(tenancy, kind, amount, day, account, lines, description, reason, posted_by):
    from django.utils import timezone

    from apps.accounting.services import create_journal_entry, post_journal_entry

    from .models import DepositTransaction

    org = tenancy.organization
    entry = create_journal_entry(
        org, entry_date=day or timezone.localdate(), description=description,
        reference=f"DEP-{str(tenancy.pk)[:8]}", lines=lines,
    )
    post_journal_entry(entry, posted_by=posted_by)
    return DepositTransaction.objects.create(
        tenancy=tenancy, kind=kind, amount=amount, transaction_date=entry.entry_date,
        account=account, reason=reason[:300], journal_entry=entry, created_by=posted_by,
    )


def _locked_tenancy(tenancy_id):
    tenancy = Tenancy.objects.select_for_update().select_related("unit__property", "tenant").filter(pk=tenancy_id).first()
    if tenancy is None:
        raise ValidationError({"tenancy": "Tenancy not found."})
    return tenancy


@transaction.atomic
def receive_deposit(tenancy_id, *, amount=None, date=None, account_id=None, posted_by=None):
    tenancy = _locked_tenancy(tenancy_id)
    if tenancy.status == Tenancy.Status.ENDED:
        raise ValidationError({"tenancy": "A deposit cannot be received for an ended tenancy."})
    summary = deposit_summary(tenancy)
    remaining_agreed = tenancy.deposit_amount - summary["received"]
    if remaining_agreed <= 0:
        raise ValidationError({"amount": "The agreed security deposit has already been received."})
    if amount in (None, ""):
        amount = remaining_agreed
    amount = _positive(amount)
    if amount > remaining_agreed:
        raise ValidationError({"amount": f"The remaining agreed deposit is {remaining_agreed:.2f}."})
    bank = _bank_or_cash(account_id)
    liability = _deposit_liability_account()
    return _post_deposit_movement(
        tenancy, "received", amount, date, bank,
        [{"account": bank, "debit": amount}, {"account": liability, "credit": amount}],
        f"Deposit received: {tenancy.tenant.display_name}, {tenancy.unit.property.name} / {tenancy.unit.label}",
        "", posted_by,
    )


@transaction.atomic
def deduct_from_deposit(tenancy_id, *, amount, reason, date=None, income_account_id=None, posted_by=None):
    tenancy = _locked_tenancy(tenancy_id)
    if tenancy.status != Tenancy.Status.ENDED or Tenancy.objects.filter(renewed_from=tenancy).exists():
        raise ValidationError({"tenancy": "Deductions are made when the tenancy has ended."})
    amount = _positive(amount)
    if not reason.strip():
        raise ValidationError({"reason": "Say what the deduction is for."})
    if amount > deposit_summary(tenancy)["held"]:
        raise ValidationError({"amount": "That is more than the deposit still held."})
    if income_account_id:
        income = Account.objects.filter(pk=income_account_id, category=Account.Category.INCOME, is_active=True).first()
    else:
        income = Account.objects.filter(
            account_type=Account.AccountType.OTHER_INCOME, is_active=True
        ).order_by("code").first()
    if income is None:
        raise ValidationError({"income_account": "Choose an active income account."})
    liability = _deposit_liability_account()
    return _post_deposit_movement(
        tenancy, "deduction", amount, date, income,
        [{"account": liability, "debit": amount}, {"account": income, "credit": amount}],
        f"Deposit kept: {reason[:100]}", reason, posted_by,
    )


@transaction.atomic
def refund_deposit(tenancy_id, *, amount=None, date=None, account_id=None, posted_by=None):
    tenancy = _locked_tenancy(tenancy_id)
    if tenancy.status != Tenancy.Status.ENDED or Tenancy.objects.filter(renewed_from=tenancy).exists():
        raise ValidationError({"tenancy": "A deposit is refunded when the tenancy has ended."})
    held = deposit_summary(tenancy)["held"]
    amount = held if amount in (None, "") else _positive(amount)
    if amount <= 0:
        raise ValidationError({"amount": "There is no deposit left to refund."})
    if amount > held:
        raise ValidationError({"amount": "That is more than the deposit still held."})
    bank = _bank_or_cash(account_id)
    liability = _deposit_liability_account()
    return _post_deposit_movement(
        tenancy, "refund", amount, date, bank,
        [{"account": liability, "debit": amount}, {"account": bank, "credit": amount}],
        f"Deposit refunded: {tenancy.tenant.display_name}", "", posted_by,
    )
