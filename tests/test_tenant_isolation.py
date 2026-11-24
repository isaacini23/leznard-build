import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.utils import DatabaseError

from apps.core.rls import assert_database_role_is_rls_safe, tenant_tables
from apps.core.tenancy import organization_context
from apps.sandbox.models import Note


def make_note(org, text):
    with organization_context(org.id):
        return Note.objects.create(text=text)


# ---- 1. application layer ----

def test_company_sees_only_its_own_rows(org_a, org_b):
    make_note(org_a, "alpha note")
    make_note(org_b, "beta note")
    with organization_context(org_a.id):
        assert list(Note.objects.values_list("text", flat=True)) == ["alpha note"]
    with organization_context(org_b.id):
        assert list(Note.objects.values_list("text", flat=True)) == ["beta note"]


def test_no_active_company_returns_nothing(org_a):
    make_note(org_a, "alpha note")
    assert Note.objects.count() == 0


def test_saving_without_active_company_is_refused(org_a):
    with pytest.raises(PermissionDenied):
        Note(text="orphan").save()


def test_cannot_save_into_another_company(org_a, org_b):
    with organization_context(org_a.id):
        with pytest.raises(PermissionDenied):
            Note(text="smuggled", organization=org_b).save()


# ---- 2. database layer (ORM filter bypassed) ----

def test_database_hides_other_company_even_without_orm_filter(org_a, org_b):
    make_note(org_a, "alpha note")
    make_note(org_b, "beta note")
    with organization_context(org_a.id):
        assert list(Note.unscoped.values_list("text", flat=True)) == ["alpha note"]


def test_database_returns_nothing_when_no_company_is_set(org_a):
    make_note(org_a, "alpha note")
    assert Note.unscoped.count() == 0


def test_database_refuses_insert_for_another_company(org_a, org_b):
    with organization_context(org_a.id):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO sandbox_note (id, organization_id, created_at, updated_at, text) "
                        "VALUES (gen_random_uuid(), %s, now(), now(), 'x')",
                        [str(org_b.id)],
                    )


def test_database_cannot_update_or_delete_other_company_rows(org_a, org_b):
    note_b = make_note(org_b, "beta note")
    with organization_context(org_a.id):
        with connection.cursor() as cur:
            cur.execute("UPDATE sandbox_note SET text = 'hacked' WHERE id = %s", [str(note_b.id)])
            assert cur.rowcount == 0
            cur.execute("DELETE FROM sandbox_note WHERE id = %s", [str(note_b.id)])
            assert cur.rowcount == 0
    with organization_context(org_b.id):
        assert Note.objects.get(id=note_b.id).text == "beta note"


def test_company_setting_is_cleared_after_the_block(org_a):
    with organization_context(org_a.id):
        pass
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.current_org', true)")
        assert cur.fetchone()[0] in ("", None)


def test_every_tenant_table_has_forced_row_level_security(db):
    tables = tenant_tables()
    assert "sandbox_note" in tables
    with connection.cursor() as cur:
        for table in tables:
            cur.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", [table]
            )
            assert cur.fetchone() == (True, True), f"{table} lacks forced row-level security"
            cur.execute("SELECT count(*) FROM pg_policies WHERE tablename = %s", [table])
            assert cur.fetchone()[0] >= 1, f"{table} has no policy"


# ---- 3. request layer ----

def test_header_without_login_is_rejected(client, org_a):
    assert client.get("/health/", HTTP_X_ORGANIZATION_ID=str(org_a.id)).status_code == 401


def test_member_can_use_their_company(client, user_a, org_a):
    client.force_login(user_a)
    assert client.get("/health/", HTTP_X_ORGANIZATION_ID=str(org_a.id)).status_code == 200


def test_non_member_cannot_name_another_company(client, user_a, org_b):
    client.force_login(user_a)
    assert client.get("/health/", HTTP_X_ORGANIZATION_ID=str(org_b.id)).status_code == 403


def test_malformed_company_id_is_rejected(client, user_a):
    client.force_login(user_a)
    assert client.get("/health/", HTTP_X_ORGANIZATION_ID="not-a-uuid").status_code == 403


def test_suspended_member_is_rejected(client, user_a, org_a):
    user_a.memberships.update(status="suspended")
    client.force_login(user_a)
    assert client.get("/health/", HTTP_X_ORGANIZATION_ID=str(org_a.id)).status_code == 403


@pytest.mark.django_db
def test_application_database_role_cannot_bypass_rls():
    assert_database_role_is_rls_safe()



def test_saving_with_explicit_company_without_active_context_is_refused(org_a):
    with pytest.raises(PermissionDenied):
        Note(text="orphan", organization=org_a).save()
