from django.db import migrations

# Existing companies get the new property permissions on their built-in roles.
# Only adds; never removes, and never touches a role a company renamed or created.
ROLE_PERMISSIONS = {
    "Manager": ["properties.lease.view", "properties.document.view", "properties.document.manage"],
    "Property officer": ["properties.lease.view", "properties.document.view", "properties.document.manage"],
    "Accountant": ["properties.lease.view"],
    "Auditor": ["properties.lease.view", "properties.document.view"],
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
    all_codes = {c for codes in ROLE_PERMISSIONS.values() for c in codes}
    for role in Role.objects.filter(name__in=ROLE_PERMISSIONS):
        role.permissions = [c for c in (role.permissions or []) if c not in all_codes]
        role.save(update_fields=["permissions"])


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0001_initial"),
        ("properties", "0002_estate_foundation"),
    ]

    operations = [migrations.RunPython(add_permissions, remove_permissions)]
