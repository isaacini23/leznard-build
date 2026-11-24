import uuid

import django.contrib.postgres.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0002_invitation"),
        ("organizations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="invitation",
            name="requested_permissions",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(max_length=100),
                blank=True,
                default=list,
                size=None,
            ),
        ),
        migrations.CreateModel(
            name="AccountantAccessGrant",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("permissions", django.contrib.postgres.fields.ArrayField(
                    base_field=models.CharField(max_length=100), blank=True, default=list, size=None
                )),
                ("status", models.CharField(
                    choices=[("active", "Active"), ("revoked", "Revoked)],
                    default="active",
                    max_length=10,
                )),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("accountant_organization", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="client_access_grants",
                    to="organizations.organization",
                )),
                ("granted_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="accountant_access_grants_created",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("organization", models.ForeignKey(
                    editable=False,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="organizations.organization",
                )),
                ("revoked_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="accountant_access_grants_revoked",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "indexes": [
                    models.Index(fields=["organization", "status"], name="acct_grant_client_status_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "accountant_organization"),
                        name="one_accountant_grant_per_client_firm",
                    ),
                ],
            },
        ),
    ]
