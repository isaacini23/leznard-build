from django.db import migrations

# Existing companies: Managers can post invoices, bills and payments (so nothing waits on an
# accountant) and run projects; Accountants can view projects and record project money;
# Auditors can view. Only adds permissions.
ROLE_PERMISSIONS = {
    "Manager": [
        "sales.invoice.post", "purchases.bill.post", "payments.payment.post",
        "construction.project.view", "construction.project.manage", "construction.payment.record",
    ],
    "Accountant": ["construction.project.view", "construction.payment.record"],
    "Auditor": ["construction.project.view"],
}


def add_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        permissions = list(role.permissions or [])
        missing = [c for c in ROLE_PERMISSIONS[role.name] if c not in permissions]
        if missing:
            role.permissions = permissions + missing
            role.save(update_fields=["permissions"])


def remove_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        role.permissions = [c for c in (role.permissions or []) if c not in ROLE_PERMISSIONS[role.name]]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0001_initial"),
        ("construction", "0001_initial"),
    ]

    operations = [migrations.RunPython(add_permissions, remove_permissions)]
