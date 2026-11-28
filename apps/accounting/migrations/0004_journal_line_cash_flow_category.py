# Add explicit line-level cash-flow classification for transactions whose
# nature cannot safely be inferred from the account type.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0003_accounting_periods"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalline",
            name="cash_flow_category",
            field=models.CharField(
                blank=True,
                choices=[
                    ("operating", "Operating"),
                    ("investing", "Investing"),
                    ("financing", "Financing"),
                    ("non_cash_adjustment", "Non-cash adjustment"),
                ],
                default="",
                max_length=24,
            ),
        ),
    ]
