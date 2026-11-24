from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TenantModel


class Role(TenantModel):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    permissions = ArrayField(models.CharField(max_length=100), default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="role_name_unique_per_org")
        ]

    def __str__(self):
        return self.name


class RoleAssignment(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="assignments")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="one_role_per_user_per_org")
        ]

    def clean(self):
        super().clean()
        if self.role_id and self.organization_id and self.role.organization_id != self.organization_id:
            raise ValidationError({"role": "The selected role must belong to this organization."})


class Invitation(TenantModel):
    """An invitation issued by an organization.

    MEMBER invitations add a person to the issuing organization.
    ACCOUNTANT_CLIENT invitations are issued by an accounting firm to a
    business owner; acceptance records the business organization selected
    by that owner. The separate client-access grant is created by the
    acceptance workflow, not by the invitation itself.
    """

    class Kind(models.TextChoices):
        MEMBER = "member", "Organization member"
        ACCOUNTANT_CLIENT = "accountant_client", "Accountant firm client"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    email = models.EmailField()
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.MEMBER)
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="invitations",
        null=True,
        blank=True,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="sent_invitations",
        null=True,
        blank=True,
    )
    # Store only a cryptographic digest of the one-time invitation token.
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="accepted_invitations",
        null=True,
        blank=True,
    )
    accepted_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        related_name="accountant_invitations_received",
        null=True,
        blank=True,
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "status"], name="invite_org_status_idx"),
            models.Index(fields=["status", "expires_at"], name="invite_status_exp_idx"),
        ]

    def clean(self):
        super().clean()
        if self.role_id and self.role.organization_id != self.organization_id:
            raise ValidationError({"role": "The selected role must belong to the issuing organization."})
        if self.kind == self.Kind.ACCOUNTANT_CLIENT and self.role_id:
            raise ValidationError({"role": "Accountant-client invitations do not assign a staff role."})
        if self.kind == self.Kind.MEMBER and self.accepted_organization_id:
            raise ValidationError(
                {"accepted_organization": "Only accountant-client invitations can reference a client organization."}
            )

    def __str__(self):
        return f"{self.email} ({self.get_kind_display()})"
