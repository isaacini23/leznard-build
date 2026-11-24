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
