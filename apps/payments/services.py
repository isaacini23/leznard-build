from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounting.models import Account
from apps.accounting.services import create_journal_entry, post_journal_entry
from apps.core.tenancy import organization_context

from .models import Payment, PaymentAllocation


def _document_paid(document, allocation_model, field_name):
    rows = allocation_model.unscoped.filter(
        organization_id=document.organization_id,
        **{field_name: document, "payment__status": Payment.Status.POSTED},
    )
    return sum((row.amount for row in rows), Decimal("0"))


@transaction.atomic
def post_payment(payment, *, posted_by=None):
    with organization_context(payment.organization_id):
        locked = Payment.unscoped.select_for_update().select_related(
            "organization", "contact", "account"
        ).get(pk=payment.pk)
        if locked.status != Payment.Status.DRAFT:
            raise ValidationError("Only draft payments can be posted.")

        allocations = list(
            PaymentAllocation.unscoped.select_for_update().filter(payment=locked)
            
        )
        allocated_total = sum((item.amount for item in allocations), Decimal("0"))
        if allocated_total > locked.amount:
            raise ValidationError("Payment allocations exceed the payment amount.")

        documents = []
        for item in allocations:
            document = item.sales_invoice if item.sales_invoice_id else item.purchase_bill
            documents.append(document)
        # Lock documents before checking outstanding balances to prevent two
        # concurrent payments from settling the same balance twice.
        if locked.payment_type == Payment.Type.CUSTOMER_RECEIPT:
            from apps.sales.models import SalesInvoice
            locked_documents = {
                str(row.pk): row for row in SalesInvoice.unscoped.select_for_update()
                .filter(pk__in=[d.pk for d in documents]).order_by("pk")
            }
            allocation_model, field_name = PaymentAllocation, "sales_invoice"
        else:
            from apps.sales.models import PurchaseBill
            locked_documents = {
                str(row.pk): row for row in PurchaseBill.unscoped.select_for_update()
                .filter(pk__in=[d.pk for d in documents]).order_by("pk")
            }
            allocation_model, field_name = PaymentAllocation, "purchase_bill"

        for item in allocations:
            document = item.sales_invoice if item.sales_invoice_id else item.purchase_bill
            current = locked_documents.get(str(document.pk))
            if current is None or current.organization_id != locked.organization_id:
                raise ValidationError("An allocated document is no longer available in this company.")
            if current.status != current.Status.POSTED:
                raise ValidationError("Payments can only be allocated to posted documents.")
            paid = _document_paid(current, allocation_model, field_name)
            if paid + item.amount > current.total:
                raise ValidationError(
                    f"Allocation exceeds the outstanding balance on {current.number}."
                )

        if locked.account.category != Account.Category.ASSET or locked.account.account_type not in (
            Account.AccountType.CASH, Account.AccountType.BANK
        ) or not locked.account.is_active:
            raise ValidationError("Choose an active cash or bank account.")

        if locked.payment_type == Payment.Type.CUSTOMER_RECEIPT:
            control_account = Account.unscoped.filter(
                organization_id=locked.organization_id,
                account_type=Account.AccountType.ACCOUNTS_RECEIVABLE,
                is_active=True,
            ).order_by("code").first()
            if control_account is None:
                raise ValidationError("No active accounts receivable account exists.")
            lines = [
                {"account": locked.account, "debit": locked.amount, "credit": 0,
                 "description": f"Customer receipt {locked.reference}"},
                {"account": control_account, "debit": 0, "credit": locked.amount,
                 "description": f"Receivable settlement: {locked.contact.display_name}"},
            ]
            description = f"Customer receipt from {locked.contact.display_name}"
            prefix = "RCPT"
        else:
            control_account = Account.unscoped.filter(
                organization_id=locked.organization_id,
                account_type=Account.AccountType.ACCOUNTS_PAYABLE,
                is_active=True,
            ).order_by("code").first()
            if control_account is None:
                raise ValidationError("No active accounts payable account exists.")
            lines = [
                {"account": control_account, "debit": locked.amount, "credit": 0,
                 "description": f"Supplier payment {locked.reference}"},
                {"account": locked.account, "debit": 0, "credit": locked.amount,
                 "description": f"Payment to: {locked.contact.display_name}"},
            ]
            description = f"Supplier payment to {locked.contact.display_name}"
            prefix = "PAY"

        journal = create_journal_entry(
            locked.organization,
            entry_date=locked.payment_date,
            description=description,
            reference=(locked.reference or f"{prefix}-{str(locked.pk)[:8]}")[:50],
            currency=locked.currency,
            exchange_rate=locked.exchange_rate,
            lines=lines,
        )
        post_journal_entry(journal, posted_by=posted_by)
        locked.status = Payment.Status.POSTED
        locked.journal_entry = journal
        locked.posted_at = timezone.now()
        locked.posted_by = posted_by
        locked.save()
        return locked
