import pytest

from apps.accounts.models import User
from apps.organizations.models import Membership, Organization


@pytest.fixture
def org_a(db):
    return Organization.objects.create(name="Alpha")


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Beta")


@pytest.fixture
def user_a(db, org_a):
    user = User.objects.create_user(email="a@example.com", password="pass12345!")
    Membership.objects.create(user=user, organization=org_a, status="active")
    return user