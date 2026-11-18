import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from django.db import DatabaseError, connection

from django.db import connection

DB_SETTING = "app.current_org"

_current_org_id = ContextVar("current_org_id", default=None)


def get_current_org_id():
    return _current_org_id.get()


def _set_db_setting(value):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, false)", [DB_SETTING, value])


@contextmanager
def organization_context(org_id):
    org_id = uuid.UUID(str(org_id))
    token = _current_org_id.set(org_id)
    _set_db_setting(str(org_id))
    try:
        yield org_id
    finally:
        try:
            _set_db_setting("")
        except DatabaseError:
            pass
        _current_org_id.reset(token)