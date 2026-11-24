import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import DatabaseError, connection

DB_SETTING = "app.current_org"

_current_org_id = ContextVar("current_org_id", default=None)


def get_current_org_id():
    return _current_org_id.get()


def _get_db_setting():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [DB_SETTING])
        return cursor.fetchone()[0]


def _set_db_setting(value):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, false)", [DB_SETTING, value])


@contextmanager
def organization_context(org_id):
    """Set and reliably restore the tenant context for this execution scope.

    The database setting is session-scoped because this context can be used
    outside an atomic block. If restoration fails, close the connection so a
    pooled/reused connection cannot carry a stale tenant into another request.
    """
    org_id = uuid.UUID(str(org_id))
    token = _current_org_id.set(org_id)
    previous_db_setting = None
    setting_changed = False

    try:
        previous_db_setting = _get_db_setting()
        _set_db_setting(str(org_id))
        setting_changed = True
        yield org_id
    finally:
        try:
            if setting_changed:
                _set_db_setting(previous_db_setting or "")
        except DatabaseError:
            # A connection whose tenant setting cannot be cleared must not be
            # reused. Closing it is safer than silently swallowing the error.
            connection.close()
        finally:
            _current_org_id.reset(token)
