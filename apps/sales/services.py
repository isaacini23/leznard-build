from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounting.models import Account
from apps.accounting.services import create_journal_entry, post_journal_entry
from apps.core.tenancy import organization_context

from .models import PurchaseBill, SalesInvoice


def _account(organization_id, account_type):
    account = Account.unscoped.filter(
        organization_id=organization_id, account_type=account_type, is_active=True
    ).order_by("code").first()
    if account is None:
        raise ValidationError(
            f"No active {account_type.replace('_', ' ')} account exists in this company."
        )
    return account


def _journal_line(account, debit=Decimal("0"), credit=Decimal("0"), description=""):
    return {"account": account, "debit": debit, "credit": credit, "description": description}


@transaction.atomic
def post_sales_invoice(invoice, *, posted_by=None):
    with organization_context(invoice.organization_id):
        locked = SalesInvoice.unscoped.select_for_update().get(pk=invoice.pk)
        if locked.status != SalesInvoice.Status.DRAFT:
            raise ValidationError("Only draft invoices can be posted.")
        lines = list(locked.lines.select_related("revenue_account", "tax_account"))
        if not lines:
            raise ValidationError("An invoice must contain at least one line.")
        ar = _account(locked.organization_id, Account.AccountType.ACCOUNTS_RECEIVABLE)
        journal_lines = [_journal_line(ar, debit=locked.total, description=f"Receivable: {locked.number}")]
        for line in lines:
            journal_lines.append(_journal_line(
                line.revenue_account, credit=line.net_amount, description=line.description
            ))
            if line.tax_amount:
                journal_lines.append(_journal_line(
                    line.tax_account, credit=line.tax_amount, description=f"Tax: {line.description}"
                ))
        journal = create_journal_entry(
            locked.organization, entry_date=locked.document_date,
            description=f"Sales invoice {locked.number}", reference=locked.number,
            currency=locked.currency, exchange_rate=locked.exchange_rate, lines=journal_lines,
        )
        post_journal_entry(journal, posted_by=posted_by)
        locked.status = SalesInvoice.Status.POSTED
        locked.journal_entry = journal
        locked.posted_at = timezone.now()
        locked.posted_by = posted_by
        locked.save()
        return locked


@transaction.atomic
def post_purchase_bill(bill, *, posted_by=None):
    with organization_context(bill.organization_id):
        locked = PurchaseBill.unscoped.select_for_update().get(pk=bill.pk)
        if locked.status != PurchaseBill.Status.DRAFT:
            raise ValidationError("Only draft bills can be posted.")
        lines = list(locked.lines.select_related("expense_account", "tax_account"))
        if not lines:
            raise ValidationError("A bill must contain at least one line.")
        ap = _account(locked.organization_id, Account.AccountType.ACCOUNTS_PAYABLE)
        journal_lines = [_journal_line(ap, credit=locked.total, description=f"Payable: {locked.number}")]
        for line in lines:
            journal_lines.append(_journal_line(
                line.expense_account, debit=line.net_amount, description=line.description
            ))
            if line.tax_amount:
                journal_lines.append(_journal_line(
                    line.tax_account, debit=line.tax_amount, description=f"Input tax: {line.description}"
                ))
        journal = create_journal_entry(
            locked.organization, entry_date=locked.document_date,
            description=f"Purchase bill {locked.number}", reference=locked.number,
            currency=locked.currency, exchange_rate=locked.exchange_rate, lines=journal_lines,
        )
        post_journal_entry(journal, posted_by=posted_by)
        locked.status = PurchaseBill.Status.POSTED
        locked.journal_entry = journal
        locked.posted_at = timezone.now()
        locked.posted_by = posted_by
        locked.save()
        return locked
