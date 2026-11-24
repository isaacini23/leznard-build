import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import DatabaseError, connection

DB_SETTING = "app.current_org"

_current_org_id = ContextVar("current_org_id", default=None)


def get_current_org_id():
    return _current_org_id.get()


def _set_db_setting(value):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, false)", [DB_SETTING, value])


@contextmanager
def organization_context(org_id):
    """Set and reliably restore tenant context for this execution scope.

    Restore from the enclosing ContextVar rather than trusting whatever value
    happens to be on the database connection. This also clears stale settings
    left on a reused connection when there is no enclosing tenant context.
    """
    org_id = uuid.UUID(str(org_id))
    previous = _current_org_id.get()
    token = _current_org_id.set(org_id)
    setting_attempted = False

    try:
        # Attempted is set first so cleanup also runs if set_config raises
        # after the server has applied the setting.
        setting_attempted = True
        _set_db_setting(str(org_id))
        yield org_id
    finally:
        try:
            if setting_attempted:
                _set_db_setting(str(previous) if previous else "")
        except DatabaseError:
            # A connection whose tenant setting cannot be cleared must not be
            # reused. Closing it is safer than silently swallowing the error.
            connection.close()
        finally:
            _current_org_id.reset(token)
