import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0003_default_role_permissions"),
        ("sales", "0002_default_role_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesinvoice",
            name="tenancy",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rent_invoices",
                to="properties.tenancy",
            ),
        ),
    ]
