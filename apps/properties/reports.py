"""Property-level profitability derived from posted invoices, receipts and purchase bills."""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

from apps.payments.models import Payment, PaymentAllocation
from apps.sales.models import PurchaseBill

from .models import Property, RentCharge


CENT = Decimal("0.01")
ZERO = Decimal("0")


def _rounded(value):
    return (value or ZERO).quantize(CENT, rounding=ROUND_HALF_UP)


def property_profitability(property_obj: Property, *, start_date, end_date):
    """Return accrual and cash performance for one property in its base currency.

    Rent is recognised from posted rent invoices. Cash receipts are recognised
    from posted allocations. Property expenses are the net (pre-input-tax)
    amounts on posted purchase bills explicitly linked to this property.
    Outstanding rent is measured as at end_date, including earlier periods.
    """
    charges = list(
        RentCharge.objects.filter(
            tenancy__unit__property=property_obj,
            invoice__status="posted",
            invoice__document_date__lte=end_date,
        ).select_related("invoice")
    )

    rent_billed = sum(
        (
            charge.invoice.total * charge.invoice.exchange_rate
            for charge in charges
            if start_date <= charge.invoice.document_date <= end_date
        ),
        ZERO,
    )

    rent_collected = ZERO
    for allocation in PaymentAllocation.objects.filter(
        sales_invoice__rent_charge__tenancy__unit__property=property_obj,
        sales_invoice__document_date__lte=end_date,
        payment__status=Payment.Status.POSTED,
        payment__payment_date__range=(start_date, end_date),
    ).select_related("payment"):
        rent_collected += allocation.amount * allocation.payment.exchange_rate

    outstanding_rent = ZERO
    for charge in charges:
        paid = sum(
            (
                allocation.amount * allocation.payment.exchange_rate
                for allocation in PaymentAllocation.objects.filter(
                    sales_invoice=charge.invoice,
                    payment__status=Payment.Status.POSTED,
                    payment__payment_date__lte=end_date,
                ).select_related("payment")
            ),
            ZERO,
        )
        outstanding_rent += max(
            charge.invoice.total * charge.invoice.exchange_rate - paid, ZERO
        )

    bills = list(
        PurchaseBill.objects.filter(
            property=property_obj,
            status=PurchaseBill.Status.POSTED,
            document_date__range=(start_date, end_date),
        ).prefetch_related("lines")
    )
    operating_expenses = sum(
        (bill.subtotal * bill.exchange_rate for bill in bills), ZERO
    )

    operating_expenses_paid = ZERO
    for allocation in PaymentAllocation.objects.filter(
        purchase_bill__property=property_obj,
        purchase_bill__document_date__lte=end_date,
        payment__status=Payment.Status.POSTED,
        payment__payment_date__range=(start_date, end_date),
    ).select_related("payment"):
        operating_expenses_paid += allocation.amount * allocation.payment.exchange_rate

    return {
        "property_id": str(property_obj.pk),
        "property_name": property_obj.name,
        "currency": property_obj.organization.base_currency.upper(),
        "start_date": start_date,
        "end_date": end_date,
        "rent_billed": _rounded(rent_billed),
        "rent_collected": _rounded(rent_collected),
        "outstanding_rent_as_of_end_date": _rounded(outstanding_rent),
        "operating_expenses": _rounded(operating_expenses),
        "operating_expenses_paid": _rounded(operating_expenses_paid),
        "net_operating_income": _rounded(rent_billed - operating_expenses),
        "cash_net_operating_income": _rounded(rent_collected - operating_expenses_paid),
        "rent_invoice_count": sum(
            1 for charge in charges
            if start_date <= charge.invoice.document_date <= end_date
        ),
        "expense_bill_count": len(bills),
    }


def owner_statement(property_obj: Property, *, start_date, end_date):
    """What the owners of a property are owed for a period: rent actually collected, less the
    manager's fee (a percentage of rent collected) and the property's repair and running costs
    billed in the period, split by ownership share. A negative net is carried forward by the
    reader, not paid out."""
    from .models import PropertyManagement, PropertyOwnership

    perf = property_profitability(property_obj, start_date=start_date, end_date=end_date)
    collected = perf["rent_collected"]
    expenses = perf["operating_expenses"]

    management = (
        PropertyManagement.objects.filter(property=property_obj)
        .exclude(start_date__gt=end_date)
        .exclude(end_date__lt=start_date)
        .order_by("-start_date")
        .select_related("manager")
        .first()
    )
    fee_percent = (management.fee_percent or ZERO) if management else ZERO
    fee = _rounded(collected * fee_percent / Decimal("100"))
    net = _rounded(collected - fee - expenses)

    # Include every ownership record that overlaps any part of the statement
    # period. Weight each share by the number of days it was effective so a former
    # owner is not dropped merely because their ownership ended before period end.
    owners = list(
        PropertyOwnership.objects.filter(property=property_obj)
        .filter(Q(start_date__isnull=True) | Q(start_date__lte=end_date))
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date))
        .select_related("owner")
        .order_by("created_at")
    )
    period_days = Decimal((end_date - start_date).days + 1)
    owner_shares = []
    for ownership in owners:
        active_start = max(start_date, ownership.start_date) if ownership.start_date else start_date
        active_end = min(end_date, ownership.end_date) if ownership.end_date else end_date
        active_days = Decimal((active_end - active_start).days + 1)
        weighted_share = ownership.share_percent * active_days / period_days
        owner_shares.append((ownership, weighted_share))

    total_weighted_share = sum((share for _, share in owner_shares), ZERO)
    shares, allocated = [], ZERO
    for index, (ownership, weighted_share) in enumerate(owner_shares):
        if index == len(owner_shares) - 1 and total_weighted_share == Decimal("100"):
            amount = net - allocated  # last owner takes the rounding remainder
        else:
            amount = _rounded(net * weighted_share / Decimal("100"))
        allocated += amount
        shares.append({
            "owner_id": str(ownership.owner_id),
            "owner": ownership.owner.display_name,
            "share_percent": ownership.share_percent,
            "period_share_percent": weighted_share.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "amount": _rounded(amount),
        })

    return {
        "property_id": str(property_obj.pk),
        "property_name": property_obj.name,
        "currency": perf["currency"],
        "start_date": start_date,
        "end_date": end_date,
        "rent_collected": collected,
        "management_fee_percent": fee_percent,
        "management_fee": fee,
        "manager": management.manager.display_name if management else None,
        "expenses": expenses,
        "net_payable_to_owners": net,
        "owners": shares,
        "unallocated": _rounded(net - sum((s["amount"] for s in shares), ZERO)),
    }
