"""Property-level profitability derived from posted invoices, receipts and purchase bills."""
from decimal import Decimal, ROUND_HALF_UP

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
