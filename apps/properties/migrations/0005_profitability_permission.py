from django.db import migrations


ROLE_PERMISSIONS = {
    "Manager": ["properties.profitability.view"],
    "Accountant": ["properties.profitability.view"],
    "Property officer": ["properties.profitability.view"],
    "Auditor": ["properties.profitability.view"],
}


def add_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        permissions = list(role.permissions or [])
        missing = [code for code in ROLE_PERMISSIONS[role.name] if code not in permissions]
        if missing:
            role.permissions = permissions + missing
            role.save(update_fields=["permissions"])


def remove_permissions(apps, schema_editor):
    Role = apps.get_model("access", "Role")
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        role.permissions = [
            code for code in (role.permissions or [])
            if code != "properties.profitability.view"
        ]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0001_initial"),
        ("properties", "0004_rent_management"),
    ]

    operations = [migrations.RunPython(add_permissions, remove_permissions)]
