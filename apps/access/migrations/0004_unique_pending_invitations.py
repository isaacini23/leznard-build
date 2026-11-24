from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0003_accountant_access_grant"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="invitation",
            constraint=models.UniqueConstraint(
                fields=("organization", "email", "kind"),
                condition=models.Q(status="pending"),
                name="one_pending_invite_per_org_email_kind",
            ),
        ),
    ]
