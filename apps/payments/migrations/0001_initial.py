# Generated for the payments and allocation module.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("organizations", "0001_initial"),
        ("contacts", "0001_initial"),
        ("accounting", "0003_accounting_periods"),
        ("sales", "0002_default_role_permissions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="Payment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("payment_type", models.CharField(choices=[("customer_receipt", "Customer receipt"), ("supplier_payment", "Supplier payment")], max_length=24)),
                ("payment_date", models.DateField()),
                ("amount", models.DecimalField(decimal_places=4, max_digits=20)),
                ("currency", models.CharField(max_length=3)),
                ("exchange_rate", models.DecimalField(decimal_places=10, default=1, max_digits=24)),
                ("method", models.CharField(choices=[("cash", "Cash"), ("bank_transfer", "Bank transfer"), ("card", "Card"), ("cheque", "Cheque"), ("direct_debit", "Direct debit"), ("other", "Other")], default="bank_transfer", max_length=20)),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("notes", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("posted", "Posted")], default="draft", max_length=10)),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="accounting.account")),
                ("contact", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="contacts.contact")),
                ("journal_entry", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment", to="accounting.journalentry")),
                ("organization", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
                ("posted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-payment_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="PaymentAllocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.DecimalField(decimal_places=4, max_digits=20)),
                ("organization", models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name="+", to="organizations.organization")),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="allocations", to="payments.payment")),
                ("purchase_bill", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment_allocations", to="sales.purchasebill")),
                ("sales_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payment_allocations", to="sales.salesinvoice")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="payment_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="paymentallocation",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="payment_allocation_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="paymentallocation",
            constraint=models.CheckConstraint(
                condition=(models.Q(("purchase_bill__isnull", True), ("sales_invoice__isnull", False)) | models.Q(("purchase_bill__isnull", False), ("sales_invoice__isnull", True))),
                name="payment_allocation_one_document",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentallocation",
            constraint=models.UniqueConstraint(condition=models.Q(("sales_invoice__isnull", False)), fields=("payment", "sales_invoice"), name="unique_payment_invoice_allocation"),
        ),
        migrations.AddConstraint(
            model_name="paymentallocation",
            constraint=models.UniqueConstraint(condition=models.Q(("purchase_bill__isnull", False)), fields=("payment", "purchase_bill"), name="unique_payment_bill_allocation"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["organization", "payment_type", "payment_date"], name="payments_org_type_date_idx"),
        ),
    ]
