from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

from .models import Account
from .services import DEFAULT_CHART_OF_ACCOUNTS, create_default_chart_of_accounts


class ChartOfAccountsTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Test Business")

    def test_new_organization_receives_default_chart(self):
        accounts = Account.unscoped.filter(organization=self.organization)
        self.assertEqual(accounts.count(), len(DEFAULT_CHART_OF_ACCOUNTS))
        self.assertTrue(accounts.filter(code="1000", is_system=True).exists())
        self.assertTrue(
            accounts.filter(code="2000", category=Account.Category.LIABILITY).exists()
        )

    def test_default_chart_creation_is_idempotent(self):
        before = Account.unscoped.filter(organization=self.organization).count()
        create_default_chart_of_accounts(self.organization)
        after = Account.unscoped.filter(organization=self.organization).count()
        self.assertEqual(before, after)

    def test_account_codes_are_unique_within_an_organization(self):
        with organization_context(self.organization.pk):
            with self.assertRaises(ValidationError):
                Account.objects.create(
                    code="1000",
                    name="Duplicate Cash",
                    category=Account.Category.ASSET,
                    account_type=Account.AccountType.CASH,
                    normal_balance=Account.NormalBalance.DEBIT,
                )

    def test_tenant_manager_only_returns_active_organizations_accounts(self):
        another_organization = Organization.objects.create(name="Another Business")
        with organization_context(self.organization.pk):
            visible_ids = set(Account.objects.values_list("organization_id", flat=True))
        self.assertEqual(visible_ids, {self.organization.pk})
        self.assertNotEqual(self.organization.pk, another_organization.pk)

    def test_parent_account_must_belong_to_same_organization(self):
        another_organization = Organization.objects.create(name="Another Business")
        with organization_context(another_organization.pk):
            foreign_parent = Account.objects.create(
                code="9999",
                name="Foreign Parent",
                category=Account.Category.ASSET,
                account_type=Account.AccountType.OTHER_ASSET,
                normal_balance=Account.NormalBalance.DEBIT,
            )

        with organization_context(self.organization.pk):
            with self.assertRaises(ValidationError):
                Account.objects.create(
                    code="9998",
                    name="Child Account",
                    category=Account.Category.ASSET,
                    account_type=Account.AccountType.OTHER_ASSET,
                    normal_balance=Account.NormalBalance.DEBIT,
                    parent=foreign_parent,
                )

    def test_system_accounts_cannot_be_deleted(self):
        account = Account.unscoped.get(organization=self.organization, code="1000")
        with self.assertRaises(ValidationError):
            account.delete()


from datetime import date
from decimal import Decimal

from .models import JournalEntry, JournalLine
from .services import create_journal_entry, post_journal_entry, reverse_journal_entry


class JournalEntryTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Journal Test Business")
        self.bank = Account.unscoped.get(organization=self.organization, code="1010")
        self.sales = Account.unscoped.get(organization=self.organization, code="4000")

    def make_journal(self, *, currency="NGN", exchange_rate=None, debit="100", credit="100"):
        return create_journal_entry(
            self.organization,
            entry_date=date(2026, 11, 24),
            description="Test transaction",
            currency=currency,
            exchange_rate=exchange_rate,
            lines=[
                {"account": self.bank, "debit": debit},
                {"account": self.sales, "credit": credit},
            ],
        )

    def test_base_currency_journal_uses_rate_one(self):
        entry = self.make_journal()
        self.assertEqual(entry.currency, "NGN")
        self.assertEqual(entry.base_currency, "NGN")
        self.assertEqual(entry.exchange_rate, Decimal("1"))

    def test_foreign_currency_journal_stores_base_equivalents(self):
        entry = self.make_journal(currency="USD", exchange_rate="1500")
        debit_line = JournalLine.unscoped.get(entry=entry, account=self.bank)
        self.assertEqual(debit_line.debit_amount, Decimal("100"))
        self.assertEqual(debit_line.base_debit_amount, Decimal("150000.0000"))

    def test_foreign_currency_requires_exchange_rate(self):
        with self.assertRaises(ValidationError):
            self.make_journal(currency="USD")

    def test_unbalanced_journal_cannot_be_posted(self):
        entry = self.make_journal(debit="100", credit="90")
        with self.assertRaises(ValidationError):
            post_journal_entry(entry)
        self.assertEqual(
            JournalEntry.unscoped.get(pk=entry.pk).status,
            JournalEntry.Status.DRAFT,
        )

    def test_balanced_journal_can_be_posted_and_is_immutable(self):
        entry = self.make_journal(currency="USD", exchange_rate="1500")
        posted = post_journal_entry(entry)
        self.assertEqual(posted.status, JournalEntry.Status.POSTED)
        line = JournalLine.unscoped.filter(entry=entry).first()
        line.description = "Attempted edit"
        with self.assertRaises(ValidationError):
            line.save()

    def test_posted_journal_can_be_reversed_once(self):
        entry = post_journal_entry(self.make_journal())
        reversal = reverse_journal_entry(entry)
        original = JournalEntry.unscoped.get(pk=entry.pk)
        self.assertEqual(original.status, JournalEntry.Status.REVERSED)
        self.assertEqual(reversal.status, JournalEntry.Status.POSTED)
        self.assertEqual(reversal.reversal_of_id, entry.pk)
        with self.assertRaises(ValidationError):
            reverse_journal_entry(original)

    def test_journal_entries_are_tenant_scoped(self):
        other = Organization.objects.create(name="Other Journal Business")
        entry = self.make_journal()
        with organization_context(other.pk):
            self.assertEqual(JournalEntry.objects.count(), 0)
        with organization_context(self.organization.pk):
            self.assertEqual(JournalEntry.objects.filter(pk=entry.pk).count(), 1)


    def test_foreign_currency_conversion_preserves_balanced_base_totals(self):
        cash = Account.unscoped.get(organization=self.organization, code="1000")
        entry = create_journal_entry(
            self.organization,
            entry_date=date(2026, 11, 24),
            description="Small foreign-currency transaction",
            currency="USD",
            exchange_rate="1.5",
            lines=[
                {"account": self.bank, "debit": "0.0001"},
                {"account": cash, "debit": "0.0001"},
                {"account": self.sales, "credit": "0.0002"},
            ],
        )
        posted = post_journal_entry(entry)
        self.assertEqual(posted.status, JournalEntry.Status.POSTED)
        lines = JournalLine.unscoped.filter(entry=entry)
        base_debits = sum((line.base_debit_amount for line in lines), Decimal("0"))
        base_credits = sum((line.base_credit_amount for line in lines), Decimal("0"))
        self.assertEqual(base_debits, base_credits)
