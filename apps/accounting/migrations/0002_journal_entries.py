# Generated for the double-entry journal foundation.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="JournalEntry",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entry_date", models.DateField()),
                ("reference", models.CharField(blank=True, max_length=50)),
                ("description", models.CharField(max_length=255)),
                ("currency", models.CharField(max_length=3)),
                ("base_currency", models.CharField(max_length=3)),
                ("exchange_rate", models.DecimalField(decimal_places=10, default=1, max_digits=24)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("posted", "Posted"), ("reversed", "Reversed")], default="draft", max_length=10)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("organization", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="posted_journal_entries", to=settings.AUTH_USER_MODEL)),
                ("reversal_of", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reversal_entry", to="accounting.journalentry")),
            ],
            options={"ordering": ["-entry_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="JournalLine",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("debit_amount", models.DecimalField(decimal_places=4, default=0, max_digits=20)),
                ("credit_amount", models.DecimalField(decimal_places=4, default=0, max_digits=20)),
                ("base_debit_amount", models.DecimalField(decimal_places=10, default=0, max_digits=28)),
                ("base_credit_amount", models.DecimalField(decimal_places=10, default=0, max_digits=28)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="journal_lines", to="accounting.account")),
                ("entry", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="accounting.journalentry")),
                ("organization", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
    ]
