from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0004_journal_line_cash_flow_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalentry",
            name="is_year_end_closing",
            field=models.BooleanField(default=False),
        ),
    ]
