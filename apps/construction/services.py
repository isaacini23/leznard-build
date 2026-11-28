"""Construction project services. Every money movement reuses the normal sales, purchase and
payment code, so the ledger, periods and audit rules apply exactly as for any other document."""
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import Account
from apps.payments.models import Payment, PaymentAllocation
from apps.payments.services import post_payment
from apps.sales.models import PurchaseBill, PurchaseBillLine, SalesInvoice, SalesInvoiceLine
from apps.sales.services import post_purchase_bill, post_sales_invoice

from .models import (
    BudgetLine,
    Project,
    ProjectCost,
    ProjectInvoice,
    Subcontract,
    SubcontractClaim,
    Variation,
)

CENT = Decimal("0.01")
ZERO = Decimal("0")


def _amount(value, field="amount", allow_zero=False):
    try:
        number = Decimal(str(value))
    except Exception:
        raise ValidationError({field: "Enter a valid amount."})
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero):
        raise ValidationError({field: "Enter an amount above zero."})
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def _org_currency(project):
    return project.organization.base_currency.upper()


def effective_contract_value(project):
    approved = Variation.objects.filter(project=project, status=Variation.Status.APPROVED).aggregate(
        t=Sum("amount")
    )["t"] or ZERO
    return project.contract_value + approved


# ---- variations --------------------------------------------------------------------------


@transaction.atomic
def decide_variation(variation_id, *, approve, decided_on=None):
    variation = Variation.objects.select_for_update().select_related("project").filter(pk=variation_id).first()
    if variation is None:
        raise ValidationError({"variation": "Variation not found."})
    if variation.status != Variation.Status.DRAFT:
        raise ValidationError({"status": "This variation has already been decided."})
    if approve and effective_contract_value(variation.project) + variation.amount < 0:
        raise ValidationError({"amount": "That omission is larger than the contract value."})
    variation.status = Variation.Status.APPROVED if approve else Variation.Status.REJECTED
    variation.decided_on = decided_on or timezone.localdate()
    variation.save()
    return variation


# ---- costs ------------------------------------------------------------------------------------


@transaction.atomic
def link_bill_to_project(project, bill_id, budget_line_id=None):
    bill = PurchaseBill.objects.filter(pk=bill_id).first()
    if bill is None:
        raise ValidationError({"purchase_bill": "Purchase bill not found."})
    if bill.status not in (PurchaseBill.Status.DRAFT, PurchaseBill.Status.POSTED):
        raise ValidationError({"purchase_bill": "Only a draft or posted bill can be a project cost."})
    if ProjectCost.objects.filter(purchase_bill=bill).exists():
        raise ValidationError({"purchase_bill": "This bill is already charged to a project."})
    line = None
    if budget_line_id:
        line = BudgetLine.objects.filter(pk=budget_line_id, project=project).first()
        if line is None:
            raise ValidationError({"budget_line": "Choose a budget line of this project."})
    return ProjectCost.objects.create(project=project, purchase_bill=bill, budget_line=line)


# ---- progress invoices -------------------------------------------------------------------------


def invoiced_to_date(project):
    total = ZERO
    for link in ProjectInvoice.objects.filter(project=project).select_related("invoice"):
        total += link.invoice.subtotal * link.invoice.exchange_rate
    return total


@transaction.atomic
def create_progress_invoice(
    project, *, amount, description="", invoice_date=None, due_days=14,
    revenue_account_id=None, post=True, posted_by=None,
):
    """Bill the client for work done. The total billed can never exceed the contract value
    (including approved variations)."""
    if project.status in (Project.Status.CANCELLED,):
        raise ValidationError({"project": "A cancelled project cannot be billed."})
    amount = _amount(amount)
    remaining = effective_contract_value(project) - invoiced_to_date(project)
    if amount > remaining:
        raise ValidationError({"amount": f"That is more than the {remaining:.2f} left to bill on this contract."})
    if revenue_account_id:
        revenue = Account.objects.filter(pk=revenue_account_id, category=Account.Category.INCOME, is_active=True).first()
    else:
        revenue = Account.objects.filter(
            account_type__in=(Account.AccountType.SERVICE_REVENUE, Account.AccountType.SALES_REVENUE), is_active=True
        ).order_by("account_type", "code").first()
    if revenue is None:
        raise ValidationError({"revenue_account": "Choose an active income account."})
    day = invoice_date or timezone.localdate()
    invoice = SalesInvoice.objects.create(
        customer=project.client,
        number=f"{project.code}-INV-{uuid.uuid4().hex[:6].upper()}",
        document_date=day,
        due_date=day + timedelta(days=int(due_days)),
        currency=_org_currency(project),
        exchange_rate=1,
        notes=f"{project.name}: {description}"[:500],
    )
    SalesInvoiceLine.objects.create(
        invoice=invoice, description=(description or f"Progress claim: {project.name}")[:255],
        quantity=1, unit_price=amount, revenue_account=revenue,
    )
    link = ProjectInvoice.objects.create(project=project, invoice=invoice, description=description[:300])
    if post:
        post_sales_invoice(invoice, posted_by=posted_by)
        link.invoice.refresh_from_db()
    return link


# ---- subcontract claims ------------------------------------------------------------------------


def _claimed(subcontract, exclude_id=None):
    qs = SubcontractClaim.objects.filter(subcontract=subcontract).exclude(status=SubcontractClaim.Status.CANCELLED)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs.aggregate(t=Sum("gross_amount"))["t"] or ZERO


@transaction.atomic
def create_claim(subcontract_id, *, gross_amount, claim_date=None, description=""):
    subcontract = Subcontract.objects.select_for_update().select_related("project").filter(pk=subcontract_id).first()
    if subcontract is None:
        raise ValidationError({"subcontract": "Subcontract not found."})
    if subcontract.status != Subcontract.Status.ACTIVE:
        raise ValidationError({"subcontract": "Only an active subcontract can take claims."})
    gross = _amount(gross_amount, "gross_amount")
    left = subcontract.contract_sum - _claimed(subcontract)
    if gross > left:
        raise ValidationError({"gross_amount": f"That is more than the {left:.2f} left on this subcontract."})
    last = SubcontractClaim.objects.filter(subcontract=subcontract).order_by("-number").first()
    retention = (gross * subcontract.retention_percent / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
    return SubcontractClaim.objects.create(
        subcontract=subcontract, number=(last.number + 1) if last else 1,
        claim_date=claim_date or timezone.localdate(), description=description[:300],
        gross_amount=gross, retention_amount=retention,
    )


def _claim_for_update(claim_id):
    claim = SubcontractClaim.objects.select_for_update(of=("self",)).select_related(
        "subcontract__project", "subcontract__subcontractor", "purchase_bill"
    ).filter(pk=claim_id).first()
    if claim is None:
        raise ValidationError({"claim": "Claim not found."})
    return claim


@transaction.atomic
def certify_claim(claim_id, *, expense_account_id=None, due_days=30, post=True, posted_by=None):
    """Accept a claim: creates the purchase bill for its gross value, charged to the project."""
    from apps.contacts.models import ContactRole

    claim = _claim_for_update(claim_id)
    if claim.status != SubcontractClaim.Status.DRAFT:
        raise ValidationError({"status": "Only a draft claim can be certified."})
    sub = claim.subcontract
    if not sub.subcontractor.roles.filter(role=ContactRole.Role.SUPPLIER).exists():
        raise ValidationError({"subcontractor": "Give this subcontractor the Supplier role so they can be paid."})
    if expense_account_id:
        expense = Account.objects.filter(pk=expense_account_id, category=Account.Category.EXPENSE, is_active=True).first()
    else:
        expense = Account.objects.filter(account_type=Account.AccountType.COST_OF_SALES, is_active=True).order_by("code").first()
    if expense is None:
        raise ValidationError({"expense_account": "Choose an active expense or cost account."})
    bill = PurchaseBill.objects.create(
        supplier=sub.subcontractor,
        number=f"{sub.project.code}-SC{claim.number}-{uuid.uuid4().hex[:6].upper()}",
        document_date=claim.claim_date,
        due_date=claim.claim_date + timedelta(days=int(due_days)),
        currency=_org_currency(sub.project),
        exchange_rate=1,
        notes=f"{sub.project.name}: {sub.description}, claim {claim.number}"[:500],
    )
    PurchaseBillLine.objects.create(
        bill=bill, description=f"{sub.description}, claim {claim.number}"[:255], quantity=1,
        unit_price=claim.gross_amount, expense_account=expense,
    )
    ProjectCost.objects.create(project=sub.project, purchase_bill=bill, budget_line=sub.budget_line)
    claim.purchase_bill = bill
    claim.status = SubcontractClaim.Status.CERTIFIED
    claim.save()
    if post:
        post_purchase_bill(bill, posted_by=posted_by)
    return SubcontractClaim.objects.select_related("purchase_bill").get(pk=claim.pk)


@transaction.atomic
def cancel_claim(claim_id):
    claim = _claim_for_update(claim_id)
    if claim.status != SubcontractClaim.Status.DRAFT:
        raise ValidationError({"status": "Only a draft claim can be cancelled."})
    claim.status = SubcontractClaim.Status.CANCELLED
    claim.save()
    return claim


def _bill_paid(bill):
    return PaymentAllocation.objects.filter(
        purchase_bill=bill, payment__status=Payment.Status.POSTED
    ).aggregate(t=Sum("amount"))["t"] or ZERO


def _pay_bill(claim, *, amount, account_id, payment_date, reference, posted_by):
    bill = claim.purchase_bill
    if bill is None or bill.status != PurchaseBill.Status.POSTED:
        raise ValidationError({"claim": "The claim's bill must be posted before it can be paid."})
    account = Account.objects.filter(pk=account_id, is_active=True).first() if account_id else None
    if account is None or account.account_type not in (Account.AccountType.BANK, Account.AccountType.CASH):
        raise ValidationError({"account": "Choose the bank or cash account the money is paid from."})
    payment = Payment.objects.create(
        payment_type=Payment.Type.SUPPLIER_PAYMENT,
        payment_date=payment_date or timezone.localdate(),
        contact=claim.subcontract.subcontractor, account=account, amount=amount,
        currency=bill.currency, exchange_rate=bill.exchange_rate,
        method="bank_transfer", reference=reference[:100],
    )
    PaymentAllocation.objects.create(payment=payment, purchase_bill=bill, amount=amount)
    post_payment(payment, posted_by=posted_by)


@transaction.atomic
def pay_claim(claim_id, *, account_id, amount=None, payment_date=None, posted_by=None):
    """Pay a certified claim. Retention is held back: you can pay at most gross minus retention
    until the retention is released."""
    claim = _claim_for_update(claim_id)
    if claim.status != SubcontractClaim.Status.CERTIFIED:
        raise ValidationError({"status": "Only a certified claim can be paid."})
    payable_now = claim.gross_amount - claim.retention_held - _bill_paid(claim.purchase_bill)
    if payable_now <= 0:
        raise ValidationError({"amount": "Nothing is payable on this claim right now."})
    amount = payable_now if amount in (None, "") else _amount(amount)
    if amount > payable_now:
        raise ValidationError({"amount": f"That is more than the {payable_now:.2f} payable now (retention is held back)."})
    _pay_bill(claim, amount=amount, account_id=account_id, payment_date=payment_date,
              reference=f"{claim.purchase_bill.number}-PAY", posted_by=posted_by)
    return SubcontractClaim.objects.select_related("purchase_bill").get(pk=claim.pk)


@transaction.atomic
def release_retention(claim_id, *, account_id, amount=None, payment_date=None, posted_by=None):
    claim = _claim_for_update(claim_id)
    if claim.status != SubcontractClaim.Status.CERTIFIED:
        raise ValidationError({"status": "Only a certified claim has retention to release."})
    held = claim.retention_held
    if held <= 0:
        raise ValidationError({"amount": "There is no retention held on this claim."})
    # the net must be paid first so the retention release is exactly what remains
    if claim.gross_amount - held - _bill_paid(claim.purchase_bill) > 0:
        raise ValidationError({"claim": "Pay the net amount of this claim before releasing its retention."})
    amount = held if amount in (None, "") else _amount(amount)
    if amount > held:
        raise ValidationError({"amount": f"That is more than the {held:.2f} retention held."})
    _pay_bill(claim, amount=amount, account_id=account_id, payment_date=payment_date,
              reference=f"{claim.purchase_bill.number}-RET", posted_by=posted_by)
    claim.retention_released += amount
    claim.save()
    return SubcontractClaim.objects.select_related("purchase_bill").get(pk=claim.pk)
