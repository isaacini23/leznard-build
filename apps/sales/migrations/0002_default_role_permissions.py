from django.db import migrations


ROLE_DOCUMENT_PERMISSIONS = {
    "Manager": [
        "sales.invoice.view", "sales.invoice.manage",
        "purchases.bill.view", "purchases.bill.manage",
    ],
    "Accountant": [
        "sales.invoice.view", "sales.invoice.manage", "sales.invoice.post",
        "purchases.bill.view", "purchases.bill.manage", "purchases.bill.post",
    ],
    "Auditor": ["sales.invoice.view", "purchases.bill.view"],
}


def add_document_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role in Role.objects.filter(name__in=ROLE_DOCUMENT_PERMISSIONS):
        permissions = list(role.permissions or [])
        changed = False
        for permission in ROLE_DOCUMENT_PERMISSIONS[role.name]:
            if permission not in permissions:
                permissions.append(permission)
                changed = True
        if changed:
            role.permissions = permissions
            role.save(update_fields=["permissions"])


def remove_document_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    all_codes = {code for codes in ROLE_DOCUMENT_PERMISSIONS.values() for code in codes}
    for role in Role.objects.filter(name__in=ROLE_DOCUMENT_PERMISSIONS):
        role.permissions = [code for code in (role.permissions or []) if code not in all_codes]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0001_initial"),
        ("sales", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(add_document_permissions, remove_document_permissions),
    ]
