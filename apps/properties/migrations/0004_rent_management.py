import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0003_accounting_periods"),
        ("organizations", "0001_initial"),
        ("properties", "0003_default_role_permissions"),
        ("sales", "0003_salesinvoice_tenancy"),
    ]

    operations = [
        migrations.CreateModel(
            name="RentSchedule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("next_invoice_date", models.DateField()),
                ("billing_day", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("due_days", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("organization", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
                ("revenue_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="rent_schedules", to="accounting.account")),
                ("tenancy", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="rent_schedule", to="properties.tenancy")),
            ],
            options={"ordering": ["next_invoice_date"]},
        ),
        migrations.CreateModel(
            name="RentCharge",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
                ("invoice", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="rent_charge", to="sales.salesinvoice")),
                ("tenancy", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="rent_charges", to="properties.tenancy")),
            ],
            options={"ordering": ["-period_start", "-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="rentcharge",
            constraint=models.UniqueConstraint(fields=("tenancy", "period_start"), name="unique_rent_charge_period"),
        ),
        migrations.AddConstraint(
            model_name="rentcharge",
            constraint=models.CheckConstraint(
                condition=models.Q(("period_end__gte", models.F("period_start"))),
                name="rent_period_end_not_before_start",
            ),
        ),
    ]
