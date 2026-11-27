"""Plain-language reports for business owners: no accounting knowledge needed.
All amounts are in the company's base currency, as strings with two decimals."""
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.payments.models import Payment, PaymentAllocation
from apps.sales.models import PurchaseBill, SalesInvoice

from .models import RentCharge, Unit

ZERO = Decimal("0")


def _money(value):
    return format(Decimal(value).quantize(Decimal("0.01")), "f")


def month_bounds(year, month):
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _paid(document_field, document):
    total = PaymentAllocation.objects.filter(
        **{document_field: document}, payment__status=Payment.Status.POSTED
    ).aggregate(t=Sum("amount"))["t"]
    return total or ZERO


def _outstanding_invoices():
    for invoice in SalesInvoice.objects.filter(status=SalesInvoice.Status.POSTED):
        due = invoice.total - _paid("sales_invoice", invoice)
        if due > 0:
            yield invoice, due


def rent_roll(today):
    """Every posted rent invoice that is not fully paid, oldest due first."""
    rows = []
    charges = RentCharge.objects.filter(invoice__status=SalesInvoice.Status.POSTED).select_related(
        "invoice", "tenancy__tenant", "tenancy__unit__property"
    )
    for charge in charges:
        due = charge.invoice.total - _paid("sales_invoice", charge.invoice)
        if due <= 0:
            continue
        overdue_days = max((today - charge.invoice.due_date).days, 0)
        rows.append({
            "charge_id": str(charge.pk),
            "tenant": charge.tenancy.tenant.display_name,
            "tenant_id": str(charge.tenancy.tenant_id),
            "property": charge.tenancy.unit.property.name,
            "unit": charge.tenancy.unit.label,
            "period_start": charge.period_start.isoformat(),
            "period_end": charge.period_end.isoformat(),
            "due_date": charge.invoice.due_date.isoformat(),
            "amount_due": _money(due),
            "days_overdue": overdue_days,
            "status": "overdue" if overdue_days else "due",
        })
    rows.sort(key=lambda r: (r["due_date"], r["tenant"]))
    return rows


def owner_summary(year, month, today):
    start, end = month_bounds(year, month)

    units = {s: Unit.objects.filter(occupancy_status=s).count() for s in Unit.Occupancy.values}
    total_units = sum(units.values())
    rentable = total_units - units.get("maintenance", 0)

    roll = rent_roll(today)
    owed = sum((Decimal(r["amount_due"]) for r in roll), ZERO)
    overdue = sum((Decimal(r["amount_due"]) for r in roll if r["days_overdue"]), ZERO)

    receipts = Payment.objects.filter(
        status=Payment.Status.POSTED, payment_date__range=(start, end)
    )
    money_in = sum((p.amount * p.exchange_rate for p in receipts.filter(payment_type=Payment.Type.CUSTOMER_RECEIPT)), ZERO)
    money_out = sum((p.amount * p.exchange_rate for p in receipts.filter(payment_type=Payment.Type.SUPPLIER_PAYMENT)), ZERO)

    rent_collected = sum(
        (a.amount * a.payment.exchange_rate for a in PaymentAllocation.objects.filter(
            payment__status=Payment.Status.POSTED,
            payment__payment_date__range=(start, end),
            sales_invoice__tenancy__isnull=False,
        ).select_related("payment")), ZERO,
    )
    rent_invoiced = sum(
        (c.invoice.total * c.invoice.exchange_rate for c in RentCharge.objects.filter(
            invoice__status=SalesInvoice.Status.POSTED, invoice__document_date__range=(start, end),
        ).select_related("invoice")), ZERO,
    )

    customers_owe = sum((due * inv.exchange_rate for inv, due in _outstanding_invoices()), ZERO)
    you_owe = ZERO
    for bill in PurchaseBill.objects.filter(status=PurchaseBill.Status.POSTED):
        due = bill.total - _paid("purchase_bill", bill)
        if due > 0:
            you_owe += due * bill.exchange_rate

    return {
        "month": f"{year:04d}-{month:02d}",
        "units": {
            "total": total_units,
            "occupied": units.get("occupied", 0),
            "vacant": units.get("vacant", 0),
            "under_maintenance": units.get("maintenance", 0),
            "occupancy_percent": _money(Decimal(units.get("occupied", 0)) * 100 / rentable) if rentable else "0.00",
        },
        "rent": {
            "invoiced_this_month": _money(rent_invoiced),
            "collected_this_month": _money(rent_collected),
            "owed_now": _money(owed),
            "overdue_now": _money(overdue),
            "tenants_with_arrears": len({r["tenant_id"] for r in roll if r["days_overdue"]}),
        },
        "money": {
            "money_in": _money(money_in),
            "money_out": _money(money_out),
            "net": _money(money_in - money_out),
            "customers_owe_you": _money(customers_owe),
            "you_owe_suppliers": _money(you_owe),
        },
    }
