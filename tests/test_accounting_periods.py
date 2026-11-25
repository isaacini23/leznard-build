from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from apps.access.models import Role, RoleAssignment
from apps.access.services import create_default_roles
from apps.accounting.models import Account, AccountingPeriod, JournalEntry
from apps.accounting.periods import period_for_date
from apps.accounting.services import create_journal_entry, post_journal_entry, reverse_journal_entry
from apps.accounts.models import User
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership, Organization

pytestmark = pytest.mark.django_db

PASSWORD = "Str0ng-pass-123"


def make_entry(org, on, amount="100.00"):
    with organization_context(org.id):
        cash = Account.objects.get(code="1000")
        capital = Account.objects.get(code="3000") if Account.objects.filter(code="3000").exists() else (
            Account.objects.filter(category=Account.Category.EQUITY).first()
        )
        return create_journal_entry(
            org,
            entry_date=on,
            description="Test",
            lines=[
                {"account": cash, "debit": amount},
                {"account": capital, "credit": amount},
            ],
        )


@pytest.fixture
def org():
    return Organization.objects.create(name="Alpha Ltd")


def test_posting_creates_the_calendar_month_period(org):
    entry = make_entry(org, date(2026, 3, 15))
    post_journal_entry(entry)
    with organization_context(org.id):
        period = AccountingPeriod.objects.get()
    assert (period.name, period.start_date, period.end_date) == (
        "2026-03", date(2026, 3, 1), date(2026, 3, 31)
    )
    assert period.status == "open"


def test_posting_into_a_closed_period_is_refused(org):
    admin = User.objects.create_user(email="a@example.com", password=PASSWORD)
    Membership.objects.create(user=admin, organization=org)
    create_default_roles(org)
    with organization_context(org.id):
        RoleAssignment.objects.create(user=admin, role=Role.objects.get(name="Administrator"))
        period = period_for_date(date(2026, 3, 10))
        from apps.accounting.periods import close_period
        close_period(period.id, closed_by=admin)

    entry = make_entry(org, date(2026, 3, 20))
    with pytest.raises(ValidationError, match="closed"):
        post_journal_entry(entry)
    with organization_context(org.id):
        assert JournalEntry.objects.get(pk=entry.pk).status == JournalEntry.Status.DRAFT
    # the next month is unaffected
    april = make_entry(org, date(2026, 4, 2))
    assert post_journal_entry(april).status == JournalEntry.Status.POSTED


def test_reversal_into_a_closed_period_is_refused_but_current_period_works(org):
    admin = User.objects.create_user(email="a@example.com", password=PASSWORD)
    Membership.objects.create(user=admin, organization=org)
    create_default_roles(org)
    with organization_context(org.id):
        RoleAssignment.objects.create(user=admin, role=Role.objects.get(name="Administrator"))
    posted = post_journal_entry(make_entry(org, date(2026, 3, 5)))
    with organization_context(org.id):
        from apps.accounting.periods import close_period
        close_period(period_for_date(date(2026, 3, 5)).id, closed_by=admin)
    with pytest.raises(ValidationError, match="closed"):
        reverse_journal_entry(posted, entry_date=date(2026, 3, 31))
    with organization_context(org.id):
        assert JournalEntry.objects.get(pk=posted.pk).status == JournalEntry.Status.POSTED
    reversal = reverse_journal_entry(posted, entry_date=date(2026, 4, 1))
    assert reversal.status == JournalEntry.Status.POSTED


def test_periods_never_overlap_and_squeeze_between_neighbours(org):
    with organization_context(org.id):
        AccountingPeriod.objects.create(name="Custom", start_date=date(2026, 3, 10), end_date=date(2026, 3, 20))
        early = period_for_date(date(2026, 3, 5))
        late = period_for_date(date(2026, 3, 25))
        assert (early.start_date, early.end_date) == (date(2026, 3, 1), date(2026, 3, 9))
        assert (late.start_date, late.end_date) == (date(2026, 3, 21), date(2026, 3, 31))
        with pytest.raises(ValidationError, match="overlaps"):
            AccountingPeriod.objects.create(name="Bad", start_date=date(2026, 3, 15), end_date=date(2026, 4, 5))


def test_other_company_periods_do_not_matter(org):
    other = Organization.objects.create(name="Beta Ltd")
    post_journal_entry(make_entry(org, date(2026, 3, 5)))
    with organization_context(other.id):
        assert AccountingPeriod.objects.count() == 0


def register(email, company):
    client = Client()
    response = client.post(
        "/api/v1/register/",
        {"email": email, "password": PASSWORD, "full_name": "Owner", "company_name": company},
        content_type="application/json",
    )
    return client, response.json()["organization"]["id"]


def test_api_lists_closes_and_reopens_periods_and_blocks_drafts():
    client, org_id = register("owner@example.com", "Acme")
    org = Organization.objects.get(id=org_id)
    draft = make_entry(org, date(2026, 5, 10))

    listed = client.get("/api/v1/periods/", HTTP_X_ORGANIZATION_ID=org_id)
    assert listed.status_code == 200 and listed.json() == []

    with organization_context(org.id):
        period = period_for_date(date(2026, 5, 10))
    url = f"/api/v1/periods/{period.id}/close/"
    blocked = client.post(url, content_type="application/json", HTTP_X_ORGANIZATION_ID=org_id)
    assert blocked.status_code == 400  # a draft is still open in May

    post_journal_entry(draft)
    closed = client.post(url, content_type="application/json", HTTP_X_ORGANIZATION_ID=org_id)
    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    again = client.post(url, content_type="application/json", HTTP_X_ORGANIZATION_ID=org_id)
    assert again.status_code == 400

    reopened = client.post(
        f"/api/v1/periods/{period.id}/reopen/", content_type="application/json", HTTP_X_ORGANIZATION_ID=org_id
    )
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"


def test_close_needs_the_period_permission_and_stays_inside_the_company():
    owner, org_id = register("owner@example.com", "Acme")
    other, other_org_id = register("second@example.com", "Beta")
    org = Organization.objects.get(id=org_id)
    with organization_context(org.id):
        period = period_for_date(date(2026, 5, 10))

    # an employee cannot close
    create_default_roles(org)
    employee = User.objects.create_user(email="emp@example.com", password=PASSWORD)
    Membership.objects.create(user=employee, organization=org)
    with organization_context(org.id):
        RoleAssignment.objects.create(user=employee, role=Role.objects.get(name="Employee"))
    emp = Client()
    emp.force_login(employee)
    url = f"/api/v1/periods/{period.id}/close/"
    assert emp.post(url, content_type="application/json", HTTP_X_ORGANIZATION_ID=org_id).status_code == 403

    # another company's administrator cannot reach it, even by naming their own company
    assert other.post(url, content_type="application/json", HTTP_X_ORGANIZATION_ID=other_org_id).status_code == 400
    assert other.post(url, content_type="application/json", HTTP_X_ORGANIZATION_ID=org_id).status_code == 403
    with organization_context(org.id):
        assert AccountingPeriod.objects.get(pk=period.pk).status == "open"
