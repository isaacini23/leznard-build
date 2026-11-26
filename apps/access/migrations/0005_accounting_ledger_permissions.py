from django.db import migrations


ACCOUNTING_PERMISSIONS = {
    "Manager": [
        "accounting.account.view", "accounting.account.manage",
        "accounting.journal.view", "accounting.journal.manage",
        "accounting.ledger.view",
    ],
    "Accountant": [
        "accounting.account.view", "accounting.account.manage",
        "accounting.journal.view", "accounting.journal.manage",
        "accounting.ledger.view",
    ],
    "Auditor": [
        "accounting.account.view", "accounting.journal.view",
        "accounting.ledger.view",
    ],
}


def add_accounting_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role_name, permissions in ACCOUNTING_PERMISSIONS.items():
        for role in Role.objects.filter(name=role_name):
            role.permissions = sorted(set(role.permissions or []) | set(permissions))
            role.save(update_fields=["permissions"])


def remove_accounting_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    remove = {permission for values in ACCOUNTING_PERMISSIONS.values() for permission in values}
    for role in Role.objects.all():
        role.permissions = [permission for permission in (role.permissions or []) if permission not in remove]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [("access", "0004_unique_pending_invitations")]
    operations = [migrations.RunPython(add_accounting_permissions, remove_accounting_permissions)]
