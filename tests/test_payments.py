from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounting.models import Account, JournalLine
from apps.accounting.services import create_default_chart_of_accounts
from apps.contacts.models import Contact, ContactRole
from apps.core.tenancy import organization_context
from apps.organizations.models import Organization
from apps.payments.models import Payment, PaymentAllocation
from apps.payments.services import post_payment
from apps.sales.models import PurchaseBill, PurchaseBillLine, SalesInvoice, SalesInvoiceLine
from apps.sales.serializers import PurchaseBillSerializer, SalesInvoiceSerializer


class PaymentWorkflowTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Payments Test Ltd", base_currency="NGN")
        with organization_context(self.org.pk):
            create_default_chart_of_accounts(self.org)
            self.customer = Contact.objects.create(display_name="Customer")
            ContactRole.objects.create(contact=self.customer, role=ContactRole.Role.CUSTOMER)
            self.supplier = Contact.objects.create(display_name="Supplier")
            ContactRole.objects.create(contact=self.supplier, role=ContactRole.Role.SUPPLIER)
            self.bank = Account.objects.get(account_type=Account.AccountType.BANK)
            self.cash = Account.objects.get(account_type=Account.AccountType.CASH)
            self.revenue = Account.objects.get(account_type=Account.AccountType.SALES_REVENUE)
            self.expense = Account.objects.get(account_type=Account.AccountType.OTHER_EXPENSE)

    def _invoice(self, number="INV-PAY-1"):
        invoice = SalesInvoice.objects.create(
            customer=self.customer, number=number, document_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31), currency="NGN",
        )
        SalesInvoiceLine.objects.create(
            invoice=invoice, description="Service", quantity=1, unit_price=1000,
            revenue_account=self.revenue,
        )
        from apps.sales.services import post_sales_invoice
        return post_sales_invoice(invoice)

    def _bill(self, number="BILL-PAY-1"):
        bill = PurchaseBill.objects.create(
            supplier=self.supplier, number=number, document_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31), currency="NGN",
        )
        PurchaseBillLine.objects.create(
            bill=bill, description="Materials", quantity=1, unit_price=800,
            expense_account=self.expense,
        )
        from apps.sales.services import post_purchase_bill
        return post_purchase_bill(bill)

    def test_customer_receipt_can_partially_allocate_and_posts_balanced_journal(self):
        with organization_context(self.org.pk):
            invoice = self._invoice()
            receipt = Payment.objects.create(
                payment_type=Payment.Type.CUSTOMER_RECEIPT, payment_date=date(2026, 1, 10),
                contact=self.customer, account=self.bank, amount=Decimal("400"),
                currency="NGN", method=Payment.Method.BANK_TRANSFER,
            )
            PaymentAllocation.objects.create(payment=receipt, sales_invoice=invoice, amount=Decimal("400"))
            posted = post_payment(receipt)
            lines = list(JournalLine.objects.filter(entry=posted.journal_entry))
            self.assertEqual(sum((x.debit_amount for x in lines), Decimal("0")), Decimal("400"))
            self.assertEqual(sum((x.credit_amount for x in lines), Decimal("0")), Decimal("400"))
            data = SalesInvoiceSerializer(invoice, context={"organization_id": self.org.pk}).data
            self.assertEqual(data["amount_paid"], "400.0000")
            self.assertEqual(data["amount_due"], "600.0000")
            self.assertEqual(data["payment_status"], "partially_paid")

    def test_supplier_payment_allocates_to_purchase_bill(self):
        with organization_context(self.org.pk):
            bill = self._bill()
            payment = Payment.objects.create(
                payment_type=Payment.Type.SUPPLIER_PAYMENT, payment_date=date(2026, 1, 10),
                contact=self.supplier, account=self.cash, amount=Decimal("300"), currency="NGN",
            )
            PaymentAllocation.objects.create(payment=payment, purchase_bill=bill, amount=Decimal("300"))
            posted = post_payment(payment)
            lines = list(JournalLine.objects.filter(entry=posted.journal_entry))
            self.assertEqual(sum((x.debit_amount for x in lines), Decimal("0")), Decimal("300"))
            self.assertEqual(sum((x.credit_amount for x in lines), Decimal("0")), Decimal("300"))
            data = PurchaseBillSerializer(bill, context={"organization_id": self.org.pk}).data
            self.assertEqual(data["amount_paid"], "300.0000")
            self.assertEqual(data["amount_due"], "500.0000")
            self.assertEqual(data["payment_status"], "partially_paid")

    def test_payment_cannot_allocate_more_than_invoice_outstanding(self):
        with organization_context(self.org.pk):
            invoice = self._invoice()
            first = Payment.objects.create(
                payment_type=Payment.Type.CUSTOMER_RECEIPT, payment_date=date(2026, 1, 10),
                contact=self.customer, account=self.bank, amount=Decimal("700"), currency="NGN",
            )
            PaymentAllocation.objects.create(payment=first, sales_invoice=invoice, amount=Decimal("700"))
            post_payment(first)
            second = Payment.objects.create(
                payment_type=Payment.Type.CUSTOMER_RECEIPT, payment_date=date(2026, 1, 11),
                contact=self.customer, account=self.bank, amount=Decimal("400"), currency="NGN",
            )
            PaymentAllocation.objects.create(payment=second, sales_invoice=invoice, amount=Decimal("400"))
            with self.assertRaises(ValidationError):
                post_payment(second)

    def test_payment_allocations_cannot_exceed_payment_amount(self):
        with organization_context(self.org.pk):
            invoice = self._invoice()
            payment = Payment.objects.create(
                payment_type=Payment.Type.CUSTOMER_RECEIPT, payment_date=date(2026, 1, 10),
                contact=self.customer, account=self.bank, amount=Decimal("200"), currency="NGN",
            )
            with self.assertRaises(ValidationError):
                PaymentAllocation.objects.create(payment=payment, sales_invoice=invoice, amount=Decimal("250"))

    def test_payment_and_document_contact_must_match(self):
        with organization_context(self.org.pk):
            payment = Payment.objects.create(
                payment_type=Payment.Type.CUSTOMER_RECEIPT, payment_date=date(2026, 1, 10),
                contact=self.customer, account=self.bank, amount=Decimal("100"), currency="NGN",
            )
            with self.assertRaises(ValidationError):
                PaymentAllocation.objects.create(payment=payment, purchase_bill=self._bill(), amount=Decimal("50"))

    def test_posted_payment_is_immutable(self):
        with organization_context(self.org.pk):
            payment = Payment.objects.create(
                payment_type=Payment.Type.CUSTOMER_RECEIPT, payment_date=date(2026, 1, 10),
                contact=self.customer, account=self.bank, amount=Decimal("100"), currency="NGN",
            )
            post_payment(payment)
            payment.notes = "Attempt to edit"
            with self.assertRaises(ValidationError):
                payment.save()
