from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

    def ready(self):
        from .rls import ensure_row_level_security

        post_migrate.connect(ensure_row_level_security, dispatch_uid="leznard_ensure_rls")