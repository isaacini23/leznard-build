from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from apps.core.tenancy import organization_context
from apps.organizations.models import Organization

from .models import Contact, ContactAddress, ContactRole


class ContactTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Test Business")
        self.other_organization = Organization.objects.create(name="Other Business")

    def create_contact(self, **kwargs):
        defaults = {
            "display_name": "Ada Lovelace",
            "contact_type": Contact.ContactType.INDIVIDUAL,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
        }
        defaults.update(kwargs)
        with organization_context(self.organization.pk):
            return Contact.objects.create(**defaults)

    def test_creates_individual_contact(self):
        contact = self.create_contact()

        self.assertEqual(contact.contact_type, Contact.ContactType.INDIVIDUAL)
        self.assertEqual(contact.first_name, "Ada")
        self.assertEqual(contact.last_name, "Lovelace")
        self.assertEqual(contact.organization_id, self.organization.pk)

    def test_creates_organization_contact(self):
        contact = self.create_contact(
            contact_type=Contact.ContactType.ORGANIZATION,
            display_name="Acme Construction",
            legal_name="Acme Construction Limited",
        )

        self.assertEqual(contact.contact_type, Contact.ContactType.ORGANIZATION)
        self.assertEqual(contact.legal_name, "Acme Construction Limited")

    def test_contact_requires_active_organization_context(self):
        with self.assertRaises(PermissionDenied):
            Contact.objects.create(display_name="No Tenant")

    def test_contact_cannot_be_saved_under_another_organization(self):
        contact = self.create_contact()
        contact.display_name = "Attempted cross-tenant update"

        with organization_context(self.other_organization.pk):
            with self.assertRaises(PermissionDenied):
                contact.save()

    def test_contact_manager_only_returns_active_organization_contacts(self):
        contact = self.create_contact()

        with organization_context(self.other_organization.pk):
            self.assertEqual(Contact.objects.count(), 0)

        with organization_context(self.organization.pk):
            self.assertEqual(list(Contact.objects.all()), [contact])


class ContactRoleTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Role Test Business")
        self.other_organization = Organization.objects.create(name="Other Role Business")
        with organization_context(self.organization.pk):
            self.contact = Contact.objects.create(display_name="BuildCo")

    def test_contact_can_have_multiple_roles(self):
        with organization_context(self.organization.pk):
            customer = ContactRole.objects.create(
                contact=self.contact,
                role=ContactRole.Role.CUSTOMER,
            )
            supplier = ContactRole.objects.create(
                contact=self.contact,
                role=ContactRole.Role.SUPPLIER,
            )

        self.assertEqual(
            set(self.contact.roles.values_list("role", flat=True)),
            {ContactRole.Role.CUSTOMER, ContactRole.Role.SUPPLIER},
        )
        self.assertEqual(customer.organization_id, self.organization.pk)
        self.assertEqual(supplier.organization_id, self.organization.pk)

    def test_duplicate_role_for_same_contact_is_rejected(self):
        with organization_context(self.organization.pk):
            ContactRole.objects.create(
                contact=self.contact,
                role=ContactRole.Role.CUSTOMER,
            )
            with self.assertRaises(ValidationError):
                ContactRole.objects.create(
                    contact=self.contact,
                    role=ContactRole.Role.CUSTOMER,
                )

    def test_role_cannot_reference_another_organizations_contact(self):
        with organization_context(self.other_organization.pk):
            with self.assertRaises(ValidationError):
                ContactRole.objects.create(
                    contact=self.contact,
                    role=ContactRole.Role.SUPPLIER,
                )


class ContactAddressTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Address Test Business")
        self.other_organization = Organization.objects.create(name="Other Address Business")
        with organization_context(self.organization.pk):
            self.contact = Contact.objects.create(display_name="Northstar Ltd")

    def create_address(self, **kwargs):
        defaults = {
            "contact": self.contact,
            "street_address": "12 Marina Road",
            "city": "Lagos",
            "state": "Lagos",
            "country": "Nigeria",
        }
        defaults.update(kwargs)
        with organization_context(self.organization.pk):
            return ContactAddress.objects.create(**defaults)

    def test_contact_can_have_multiple_addresses(self):
        office = self.create_address(address_type=ContactAddress.AddressType.OFFICE)
        site = self.create_address(
            address_type=ContactAddress.AddressType.SITE,
            street_address="Plot 4, Industrial Layout",
            city="Uyo",
            state="Akwa Ibom",
        )

        with organization_context(self.organization.pk):
            self.assertEqual(self.contact.addresses.count(), 2)
        self.assertNotEqual(office.pk, site.pk)

    def test_only_one_default_address_is_allowed_per_contact(self):
        self.create_address(is_default=True)

        with self.assertRaises(ValidationError):
            self.create_address(
                address_type=ContactAddress.AddressType.BILLING,
                is_default=True,
            )

    def test_non_default_addresses_can_coexist(self):
        self.create_address(is_default=False)
        self.create_address(
            address_type=ContactAddress.AddressType.SHIPPING,
            is_default=False,
        )

        with organization_context(self.organization.pk):
            self.assertEqual(self.contact.addresses.count(), 2)

    def test_address_cannot_reference_another_organizations_contact(self):
        with organization_context(self.other_organization.pk):
            with self.assertRaises(ValidationError):
                ContactAddress.objects.create(
                    contact=self.contact,
                    street_address="1 Foreign Street",
                    city="Abuja",
                )


class ContactHistoryTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="History Test Business")

    def test_deactivating_contact_preserves_roles_and_addresses(self):
        with organization_context(self.organization.pk):
            contact = Contact.objects.create(display_name="Legacy Supplier")
            ContactRole.objects.create(
                contact=contact,
                role=ContactRole.Role.SUPPLIER,
            )
            ContactAddress.objects.create(
                contact=contact,
                street_address="8 Old Market Road",
                city="Uyo",
            )

            contact.is_active = False
            contact.save()

            saved_contact = Contact.objects.get(pk=contact.pk)
            self.assertFalse(saved_contact.is_active)
            self.assertEqual(saved_contact.roles.count(), 1)
            self.assertEqual(saved_contact.addresses.count(), 1)
