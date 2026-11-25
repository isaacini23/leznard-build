from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.services import create_default_chart_of_accounts
from apps.contacts.models import Contact, ContactRole
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

from .models import PurchaseBill, PurchaseBillLine, SalesInvoice, SalesInvoiceLine
from .services import post_purchase_bill, post_sales_invoice


class SalesPurchaseDocumentTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Document Test Ltd", base_currency="NGN")
        with organization_context(self.org.pk):
            create_default_chart_of_accounts(self.org)
            self.customer = Contact.objects.create(display_name="Customer")
            ContactRole.objects.create(contact=self.customer, role=ContactRole.Role.CUSTOMER)
            self.supplier = Contact.objects.create(display_name="Supplier")
            ContactRole.objects.create(contact=self.supplier, role=ContactRole.Role.SUPPLIER)
            self.revenue = Account.objects.get(account_type=Account.AccountType.SALES_REVENUE)
            self.expense = Account.objects.get(account_type=Account.AccountType.OTHER_EXPENSE)
            self.vat = Account.objects.get(account_type=Account.AccountType.VAT_PAYABLE)
            self.ar = Account.objects.get(account_type=Account.AccountType.ACCOUNTS_RECEIVABLE)
            self.ap = Account.objects.get(account_type=Account.AccountType.ACCOUNTS_PAYABLE)

    def test_sales_invoice_totals_and_posting_create_balanced_journal(self):
        with organization_context(self.org.pk):
            invoice = SalesInvoice.objects.create(
                customer=self.customer, number="INV-001",
                document_date=date(2026, 1, 10), due_date=date(2026, 1, 20), currency="NGN",
            )
            SalesInvoiceLine.objects.create(
                invoice=invoice, description="Consulting", quantity=2,
                unit_price=Decimal("1000"), discount_amount=Decimal("100"),
                tax_amount=Decimal("90"), revenue_account=self.revenue, tax_account=self.vat,
            )
            self.assertEqual(invoice.subtotal, Decimal("1900.0000"))
            self.assertEqual(invoice.tax_total, Decimal("90.0000"))
            self.assertEqual(invoice.total, Decimal("1990.0000"))
            posted = post_sales_invoice(invoice)
            self.assertEqual(posted.status, SalesInvoice.Status.POSTED)
            journal = posted.journal_entry
            self.assertEqual(journal.status, JournalEntry.Status.POSTED)
            lines = list(JournalLine.objects.filter(entry=journal))
            self.assertEqual(sum((x.debit_amount for x in lines), Decimal("0")), Decimal("1990"))
            self.assertEqual(sum((x.credit_amount for x in lines), Decimal("0")), Decimal("1990"))

    def test_purchase_bill_posts_expense_and_input_tax_to_payable(self):
        with organization_context(self.org.pk):
            input_tax = Account.objects.create(
                code="1210", name="Input VAT", category=Account.Category.ASSET,
                account_type=Account.AccountType.OTHER_ASSET,
                normal_balance=Account.NormalBalance.DEBIT,
            )
            bill = PurchaseBill.objects.create(
                supplier=self.supplier, number="BILL-001", supplier_reference="SUP-88",
                document_date=date(2026, 1, 10), due_date=date(2026, 1, 20), currency="NGN",
            )
            PurchaseBillLine.objects.create(
                bill=bill, description="Office supplies", quantity=1, unit_price=500,
                tax_amount=75, expense_account=self.expense, tax_account=input_tax,
            )
            posted = post_purchase_bill(bill)
            self.assertEqual(posted.status, PurchaseBill.Status.POSTED)
            lines = list(JournalLine.objects.filter(entry=posted.journal_entry))
            self.assertEqual(sum((x.debit_amount for x in lines), Decimal("0")), Decimal("575"))
            self.assertEqual(sum((x.credit_amount for x in lines), Decimal("0")), Decimal("575"))

    def test_document_requires_lines_before_posting(self):
        with organization_context(self.org.pk):
            invoice = SalesInvoice.objects.create(
                customer=self.customer, number="INV-EMPTY",
                document_date=date(2026, 1, 10), due_date=date(2026, 1, 20), currency="NGN",
            )
            with self.assertRaises(ValidationError):
                post_sales_invoice(invoice)

    def test_posted_invoice_cannot_be_edited(self):
        with organization_context(self.org.pk):
            invoice = SalesInvoice.objects.create(
                customer=self.customer, number="INV-002",
                document_date=date(2026, 1, 10), due_date=date(2026, 1, 20), currency="NGN",
            )
            SalesInvoiceLine.objects.create(
                invoice=invoice, description="Work", quantity=1, unit_price=100,
                revenue_account=self.revenue,
            )
            post_sales_invoice(invoice)
            invoice.notes = "Changed after posting"
            with self.assertRaises(ValidationError):
                invoice.save()

    def test_customer_from_another_organization_is_rejected(self):
        other = Organization.objects.create(name="Other Ltd")
        with organization_context(self.org.pk):
            with organization_context(other.pk):
                foreign = Contact.objects.create(display_name="Foreign")
            with self.assertRaises(ValidationError):
                SalesInvoice.objects.create(
                    customer=foreign, number="INV-FOREIGN",
                    document_date=date(2026, 1, 10), due_date=date(2026, 1, 20), currency="NGN",
                )
