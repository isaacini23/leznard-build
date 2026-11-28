from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0005_journal_entry_year_end_closing"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalentry",
            name="closing_period_end",
            field=models.DateField(blank=True, null=True),
        ),
    ]
