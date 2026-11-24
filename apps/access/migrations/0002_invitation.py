# Generated for the accountant and organization invitation foundation.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("access", "0001_initial"),
        ("organizations", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Invitation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("email", models.EmailField(max_length=254)),
                ("kind", models.CharField(
                    choices=[
                        ("member", "Organization member"),
                        ("accountant_client", "Accountant firm client"),
                    ],
                    default="member",
                    max_length=24,
                )),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Pending"),
                        ("accepted", "Accepted"),
                        ("revoked", "Revoked"),
                        ("expired", "Expired"),
                    ],
                    default="pending",
                    max_length=10,
                )),
                ("expires_at", models.DateTimeField()),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("accepted_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="accepted_invitations",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("accepted_organization", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="accountant_invitations_received",
                    to="organizations.organization",
                )),
                ("invited_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="sent_invitations",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("organization", models.ForeignKey(
                    editable=False,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="+",
                    to="organizations.organization",
                )),
                ("role", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="invitations",
                    to="access.role",
                )),
            ],
            options={
                "indexes": [
                    models.Index(fields=["organization", "status"], name="invite_org_status_idx"),
                    models.Index(fields=["status", "expires_at"], name="invite_status_exp_idx"),
                ],
            },
        ),
    ]
