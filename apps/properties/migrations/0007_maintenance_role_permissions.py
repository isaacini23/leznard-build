from django.db import migrations

# Existing companies get maintenance permissions on their built-in roles. Only adds.
ROLE_PERMISSIONS = {
    "Manager": ["properties.maintenance.view", "properties.maintenance.manage"],
    "Property officer": ["properties.maintenance.view", "properties.maintenance.manage"],
    "Employee": ["properties.maintenance.view"],
    "Accountant": ["properties.maintenance.view"],
    "Auditor": ["properties.maintenance.view"],
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
    codes = {c for values in ROLE_PERMISSIONS.values() for c in values}
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        role.permissions = [c for c in (role.permissions or []) if c not in codes]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0001_initial"),
        ("properties", "0006_maintenance_requests"),
    ]

    operations = [migrations.RunPython(add_permissions, remove_permissions)]
