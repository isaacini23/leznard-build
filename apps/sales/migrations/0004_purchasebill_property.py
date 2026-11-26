import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("properties", "0004_rent_management"),
        ("sales", "0003_salesinvoice_tenancy"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasebill",
            name="property",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_bills",
                to="properties.property",
            ),
        ),
    ]
