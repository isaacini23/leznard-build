from django.db import migrations


ROLE_PERMISSIONS = {
    "Manager": ["payments.payment.view", "payments.payment.manage"],
    "Accountant": ["payments.payment.view", "payments.payment.manage", "payments.payment.post"],
    "Auditor": ["payments.payment.view"],
}


def add_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        permissions = list(role.permissions or [])
        for code in ROLE_PERMISSIONS[role.name]:
            if code not in permissions:
                permissions.append(code)
        role.permissions = permissions
        role.save(update_fields=["permissions"])


def remove_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    codes = {code for values in ROLE_PERMISSIONS.values() for code in values}
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        role.permissions = [code for code in (role.permissions or []) if code not in codes]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0001_initial"),
        ("access", "0004_unique_pending_invitations"),
    ]
    operations = [migrations.RunPython(add_permissions, remove_permissions)]
