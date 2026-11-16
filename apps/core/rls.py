from django.apps import apps as django_apps
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
    with connection.cursor() as cursor:
        for table in tenant_tables():
            for statement in enable_sql(table):
                cursor.execute(statement)