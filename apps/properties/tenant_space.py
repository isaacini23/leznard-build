"""Late fees, reminders, owner payouts and the tenant portal."""
import hashlib
import secrets
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounting.models import Account
from apps.core.tenancy import organization_context
from apps.sales.models import SalesInvoice, SalesInvoiceLine

from .models import (
    MaintenanceRequest,
    OwnerPayout,
    PortalInvitation,
    RentCharge,
    Tenancy,
    TenantPortalAccess,
)
from .owner_reports import rent_roll
from .services import post_rent_invoice, rent_amount_paid

CENT = Decimal("0.01")
ZERO = Decimal("0")


def _money(value):
    return format(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP), "f")


# ---- late fees --------------------------------------------------------------------


@transaction.atomic
def apply_late_fees(today, *, post=False, posted_by=None):
    """Charge each overdue rent invoice its late fee once, as a separate invoice to the tenant.
    The fee is the schedule's flat amount plus its percentage of what is still owed, charged
    after the grace period. Never charged twice for the same rent."""
    from apps.sales.services import post_sales_invoice

    created, skipped = [], 0
    charges = RentCharge.objects.filter(
        invoice__status=SalesInvoice.Status.POSTED, late_fee_invoice__isnull=True
    ).select_related("invoice", "tenancy__tenant", "tenancy__unit__property")
    for charge in charges:
        schedule = charge.tenancy.rent_schedule if hasattr(charge.tenancy, "rent_schedule") else None
        if schedule is None or (schedule.late_fee_percent <= 0 and schedule.late_fee_flat <= 0):
            continue
        if charge.invoice.due_date + timedelta(days=schedule.late_fee_grace_days) >= today:
            continue
        due = charge.invoice.total - rent_amount_paid(charge.invoice)
        if due <= 0:
            continue
        fee = (schedule.late_fee_flat + due * schedule.late_fee_percent / Decimal("100")).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
        if fee <= 0:
            skipped += 1
            continue
        tenancy = charge.tenancy
        label = f"{tenancy.unit.property.name} / {tenancy.unit.label}"
        invoice = SalesInvoice.objects.create(
            customer=tenancy.tenant,
            number=f"LATE-{today:%Y%m}-{uuid.uuid4().hex[:8].upper()}",
            document_date=today,
            due_date=today + timedelta(days=schedule.due_days),
            currency=charge.invoice.currency,
            exchange_rate=charge.invoice.exchange_rate,
            notes=f"Late payment fee for rent invoice {charge.invoice.number}",
        )
        SalesInvoiceLine.objects.create(
            invoice=invoice,
            description=f"Late payment fee: rent {charge.period_start.isoformat()} to "
                        f"{charge.period_end.isoformat()} ({label})",
            quantity=1, unit_price=fee, revenue_account=schedule.revenue_account,
        )
        charge.late_fee_invoice = invoice
        charge.save(update_fields=["late_fee_invoice", "updated_at"])
        if post:
            post_sales_invoice(invoice, posted_by=posted_by)
        created.append({"tenant": tenancy.tenant.display_name, "unit": tenancy.unit.label,
                        "invoice": invoice.number, "fee": _money(fee)})
    return {"charged": len(created), "fees": created, "skipped": skipped}


# ---- reminders --------------------------------------------------------------------


def rent_reminders(today, *, days_before=3):
    """Ready-to-send reminder texts for rent that is overdue or falls due within `days_before` days."""
    from apps.organizations.models import Organization

    from apps.core.tenancy import get_current_org_id

    company = Organization.objects.filter(pk=get_current_org_id()).first()
    currency = company.base_currency.upper() if company else ""
    sign_off = company.name if company else "Your landlord"
    contacts = {}
    from apps.contacts.models import Contact

    rows = []
    for row in rent_roll(today):
        due_date = date.fromisoformat(row["due_date"])
        days_to_due = (due_date - today).days
        if days_to_due > days_before:
            continue
        contact = contacts.get(row["tenant_id"]) or Contact.objects.filter(pk=row["tenant_id"]).first()
        contacts[row["tenant_id"]] = contact
        where = f"{row['property']}, {row['unit']}"
        if row["days_overdue"]:
            message = (
                f"Dear {row['tenant']}, your rent of {currency} {Decimal(row['amount_due']):,.2f} for {where} "
                f"was due on {due_date:%d %b %Y} and is now {row['days_overdue']} day(s) overdue. "
                f"Please make payment as soon as possible. Thank you. {sign_off}"
            )
            kind = "overdue"
        else:
            message = (
                f"Dear {row['tenant']}, a friendly reminder that your rent of {currency} "
                f"{Decimal(row['amount_due']):,.2f} for {where} is due on {due_date:%d %b %Y}. "
                f"Thank you. {sign_off}"
            )
            kind = "upcoming"
        rows.append({**row, "kind": kind, "phone": contact.phone if contact else "",
                     "email": contact.email if contact else "", "message": message})
    return rows


# ---- owner payouts ------------------------------------------------------------------

OWNER_PAYOUT_ACCOUNT_CODE = "3300"


def _payout_account():
    account = Account.objects.filter(code=OWNER_PAYOUT_ACCOUNT_CODE).first()
    if account is None:
        account = Account.objects.create(
            code=OWNER_PAYOUT_ACCOUNT_CODE, name="Owner Payouts",
            category=Account.Category.EQUITY, account_type=Account.AccountType.DRAWINGS,
            normal_balance=Account.NormalBalance.DEBIT, is_system=True,
        )
    return account


def paid_to_owner(property_obj, owner_id, period_start, period_end):
    total = sum(
        (p.amount for p in OwnerPayout.objects.filter(
            property=property_obj, owner_id=owner_id, period_start=period_start, period_end=period_end
        )), ZERO,
    )
    return total


@transaction.atomic
def record_owner_payout(
    property_obj, *, owner_id, period_start, period_end, amount=None, payout_date=None,
    account_id=None, reference="", posted_by=None,
):
    """Pay an owner what the period's statement says they are owed (or part of it) and post it."""
    from apps.accounting.services import create_journal_entry, post_journal_entry

    from .reports import owner_statement
    from .services import _bank_or_cash, _positive

    statement = owner_statement(property_obj, start_date=period_start, end_date=period_end)
    share = next((o for o in statement["owners"] if o["owner_id"] == str(owner_id)), None)
    if share is None:
        raise ValidationError({"owner": "This contact is not an owner of the property for that period."})
    owed = share["amount"] - paid_to_owner(property_obj, owner_id, period_start, period_end)
    if owed <= 0:
        raise ValidationError({"amount": "Nothing is owed to this owner for that period."})
    amount = owed if amount in (None, "") else _positive(amount)
    if amount > owed:
        raise ValidationError({"amount": f"That is more than the {owed:.2f} still owed to this owner."})
    bank = _bank_or_cash(account_id)
    payouts = _payout_account()
    day = payout_date or timezone.localdate()
    entry = create_journal_entry(
        property_obj.organization, entry_date=day,
        description=f"Owner payout: {share['owner']}, {property_obj.name}, {period_start} to {period_end}",
        reference=(reference or f"PAYOUT-{str(property_obj.pk)[:8]}")[:50],
        lines=[{"account": payouts, "debit": amount}, {"account": bank, "credit": amount}],
    )
    post_journal_entry(entry, posted_by=posted_by)
    return OwnerPayout.objects.create(
        property=property_obj, owner_id=owner_id, period_start=period_start, period_end=period_end,
        amount=amount, payout_date=day, account=bank, reference=reference[:100],
        journal_entry=entry, created_by=posted_by,
    )


# ---- tenant portal --------------------------------------------------------------------


def create_portal_invitation(contact, *, created_by=None, expires_in_days=14):
    from apps.contacts.models import ContactRole

    if not contact.roles.filter(role=ContactRole.Role.TENANT).exists():
        raise ValidationError({"contact": "Only a tenant can be invited to the tenant portal."})
    if TenantPortalAccess.objects.filter(contact=contact, is_active=True).exists():
        raise ValidationError({"contact": "This tenant already has portal access."})
    secret = secrets.token_urlsafe(32)
    invitation = PortalInvitation.objects.create(
        contact=contact, token_hash=hashlib.sha256(secret.encode()).hexdigest(),
        expires_at=timezone.now() + timedelta(days=expires_in_days), created_by=created_by,
    )
    return invitation, f"{contact.organization_id}.{secret}"


@transaction.atomic
def accept_portal_invitation(user, token):
    """Link the signed-in user to the invited tenant. One person per tenant."""
    try:
        org_text, secret = str(token).split(".", 1)
        org_id = uuid.UUID(org_text)
    except (ValueError, AttributeError):
        raise ValidationError({"token": "This invitation code is invalid."})
    invalid = ValidationError({"token": "This invitation code is invalid or has expired."})
    token_hash = hashlib.sha256(secret.encode()).hexdigest()
    with organization_context(org_id):
        invitation = PortalInvitation.objects.select_for_update(of=("self",)).select_related("contact").filter(
            token_hash=token_hash
        ).first()
        if invitation is None or invitation.accepted_at or invitation.expires_at <= timezone.now():
            raise invalid
        if TenantPortalAccess.objects.filter(contact=invitation.contact, is_active=True).exists():
            raise invalid
        access, _ = TenantPortalAccess.objects.get_or_create(
            contact=invitation.contact, defaults={"user": user, "organization_id": org_id},
        )
        if access.user_id != user.pk or not access.is_active:
            access.user, access.is_active, access.organization_id = user, True, org_id
            access.save()
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at", "updated_at"])
    return access


def portal_rent(contact):
    """The tenant's own posted rent invoices, newest first, with what is paid and owed."""
    today = timezone.localdate()
    rows = []
    charges = RentCharge.objects.filter(
        tenancy__tenant=contact, invoice__status=SalesInvoice.Status.POSTED
    ).select_related("invoice", "tenancy__unit__property").order_by("-period_start")
    for charge in charges:
        paid = rent_amount_paid(charge.invoice)
        due = charge.invoice.total - paid
        if due <= 0:
            state = "paid"
        elif charge.invoice.due_date < today:
            state = "overdue"
        else:
            state = "part_paid" if paid > 0 else "due"
        rows.append({
            "invoice_number": charge.invoice.number,
            "property": charge.tenancy.unit.property.name,
            "unit": charge.tenancy.unit.label,
            "period_start": charge.period_start.isoformat(),
            "period_end": charge.period_end.isoformat(),
            "due_date": charge.invoice.due_date.isoformat(),
            "amount": _money(charge.invoice.total),
            "paid": _money(paid),
            "balance": _money(max(due, ZERO)),
            "status": state,
        })
    return rows


def portal_report_repair(contact, *, unit_id, title, description="", category="other", priority="normal"):
    tenancy = Tenancy.objects.filter(
        tenant=contact, status=Tenancy.Status.ACTIVE, unit_id=unit_id
    ).select_related("unit__property").first()
    if tenancy is None:
        raise ValidationError({"unit": "You can only report repairs for a home you currently rent."})
    if not str(title).strip():
        raise ValidationError({"title": "Tell us what needs fixing."})
    return MaintenanceRequest.objects.create(
        property=tenancy.unit.property, unit=tenancy.unit, reported_by=contact,
        title=str(title)[:200], description=str(description), category=category, priority=priority,
        reported_date=timezone.localdate(),
    )
