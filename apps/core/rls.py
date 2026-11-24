from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

POLICY = "tenant_isolation"
_EXPR = "organization_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def tenant_tables():
    from .models import TenantModel

    return sorted(
        m._meta.db_table
        for m in django_apps.get_models()
        if issubclass(m, TenantModel) and not m._meta.abstract and not m._meta.proxy
    )


def assert_database_role_is_rls_safe():
    """Reject PostgreSQL roles that can bypass row-level security."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
        role_flags = cursor.fetchone()

    if role_flags is None or role_flags[0] or role_flags[1]:
        raise ImproperlyConfigured(
            "Leznard's application database role must be neither SUPERUSER "
            "nor BYPASSRLS. Configure a dedicated least-privilege PostgreSQL role."
        )


def enable_sql(table):
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;',
        f'DROP POLICY IF EXISTS {POLICY} ON "{table}";',
        f'CREATE POLICY {POLICY} ON "{table}" USING ({_EXPR}) WITH CHECK ({_EXPR});',
    ]


def ensure_row_level_security(**kwargs):
    if connection.vendor != "postgresql":
        return
    assert_database_role_is_rls_safe()
    with connection.cursor() as cursor:
        for table in tenant_tables():
            for statement in enable_sql(table):
                cursor.execute(statement)
