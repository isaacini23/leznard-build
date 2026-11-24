import hashlib
import secrets
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.tenancy import get_current_org_id, organization_context
from apps.organizations.models import Membership

from .models import AccountantAccessGrant, Invitation, Role, RoleAssignment
from .permissions import ALL, DEFAULT_ROLES


def create_default_roles(organization):
    with organization_context(organization.id):
        for name, description, codes in DEFAULT_ROLES:
            Role.objects.get_or_create(
                name=name,
                defaults={"description": description, "permissions": codes},
            )


def _has_active_membership(user, organization_id):
    return Membership.objects.filter(
        user=user,
        organization_id=organization_id,
        status=Membership.Status.ACTIVE,
    ).exists()


def get_permissions(user, organization_id=None):
    """Return permissions only for a user's active membership or client grant."""
    if not user.is_authenticated:
        return set()

    active_org_id = get_current_org_id()
    if active_org_id is None:
        return set()

    requested_org_id = organization_id or active_org_id
    if str(requested_org_id) != str(active_org_id):
        return set()

    if _has_active_membership(user, active_org_id):
        assignment = (
            RoleAssignment.objects.filter(
                user=user,
                organization_id=active_org_id,
                role__organization_id=active_org_id,
            )
            .select_related("role")
            .first()
        )
        return set(assignment.role.permissions) if assignment else set()

    # External accountants do not receive a client staff role. Their
    # permissions come only from the client-owned grant.
    grants = AccountantAccessGrant.objects.filter(
        organization_id=active_org_id,
        accountant_organization__memberships__user=user,
        accountant_organization__memberships__status=Membership.Status.ACTIVE,
        status=AccountantAccessGrant.Status.ACTIVE,
    ).distinct()
    granted = set()
    for grant in grants:
        granted.update(grant.permissions)
    return granted


def has_permission(user, code, organization_id=None):
    granted = get_permissions(user, organization_id=organization_id)
    return ALL in granted or code in granted


def create_accountant_invitation(
    *, invited_by, email, requested_permissions, expires_in_days=14
):
    firm_id = get_current_org_id()
    if firm_id is None:
        raise PermissionDenied("Select your accounting firm's organization first.")
    if not _has_active_membership(invited_by, firm_id):
        raise PermissionDenied("You must be an active member of the issuing firm.")
    if not has_permission(invited_by, "access.user.invite", firm_id):
        raise PermissionDenied("You do not have permission to invite clients.")
    if not 1 <= expires_in_days <= 30:
        raise ValidationError({"expires_in_days": "Invitation expiry must be between 1 and 30 days."})

    secret = secrets.token_urlsafe(32)
    token = f"{firm_id}.{secret}"
    token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()

    normalized_email = email.strip().lower()
    with transaction.atomic(), organization_context(firm_id):
        now = timezone.now()
        previous = Invitation.objects.select_for_update().filter(
            email=normalized_email,
            kind=Invitation.Kind.ACCOUNTANT_CLIENT,
            status=Invitation.Status.PENDING,
        ).first()
        if previous and previous.expires_at <= now:
            previous.status = Invitation.Status.EXPIRED
            previous.save(update_fields=["status", "updated_at"])
            previous = None
        if previous:
            raise ValidationError({"email": "A pending invitation already exists for this email."})

        invitation = Invitation.objects.create(
            email=normalized_email,
            kind=Invitation.Kind.ACCOUNTANT_CLIENT,
            requested_permissions=sorted(set(requested_permissions)),
            invited_by=invited_by,
            token_hash=token_hash,
            expires_at=now + timedelta(days=expires_in_days),
        )
    return invitation, token


def accept_accountant_invitation(
    *, token, accepted_by, client_organization_id, approved_permissions
):
    try:
        firm_text, secret = token.split(".", 1)
        firm_id = uuid.UUID(firm_text)
        client_id = uuid.UUID(str(client_organization_id))
    except (AttributeError, TypeError, ValueError):
        raise ValidationError({"token": "This invitation link is invalid."})

    token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    grant = None
    expired = False
    with transaction.atomic():
        with organization_context(firm_id):
            try:
                invitation = Invitation.objects.select_for_update().get(
                    token_hash=token_hash,
                    kind=Invitation.Kind.ACCOUNTANT_CLIENT,
                )
            except Invitation.DoesNotExist:
                raise ValidationError({"token": "This invitation link is invalid."})

            if invitation.status != Invitation.Status.PENDING:
                raise ValidationError({"token": "This invitation is no longer available."})
            if invitation.expires_at <= timezone.now():
                invitation.status = Invitation.Status.EXPIRED
                invitation.save(update_fields=["status", "updated_at"])
                expired = True
            else:
                if accepted_by.email.strip().lower() != invitation.email.strip().lower():
                    raise PermissionDenied(
                        "Sign in with the email address that received this invitation."
                    )
                if not _has_active_membership(accepted_by, client_id):
                    raise PermissionDenied(
                        "You must be an active member of the business you select."
                    )

                approved = set(approved_permissions)
                requested = set(invitation.requested_permissions)
                if not approved.issubset(requested):
                    raise ValidationError(
                        {"approved_permissions": "You can approve only permissions requested by the firm."}
                    )

                with organization_context(client_id):
                    grant, created = AccountantAccessGrant.objects.get_or_create(
                        accountant_organization=firm_id,
                        defaults={
                            "permissions": sorted(approved),
                            "status": AccountantAccessGrant.Status.ACTIVE,
                            "granted_by": accepted_by,
                        },
                    )
                    if not created:
                        grant.permissions = sorted(approved)
                        grant.status = AccountantAccessGrant.Status.ACTIVE
                        grant.granted_by = accepted_by
                        grant.revoked_by = None
                        grant.revoked_at = None
                        grant.save()

                invitation.status = Invitation.Status.ACCEPTED
                invitation.accepted_by = accepted_by
                invitation.accepted_organization_id = client_id
                invitation.accepted_at = timezone.now()
                invitation.save(
                    update_fields=[
                        "status", "accepted_by", "accepted_organization",
                        "accepted_at", "updated_at",
                    ]
                )

    if expired:
        raise ValidationError({"token": "This invitation has expired."})
    return grant


def revoke_accountant_access(*, grant_id, revoked_by):
    client_id = get_current_org_id()
    if client_id is None:
        raise PermissionDenied("Select the client company first.")
    if not _has_active_membership(revoked_by, client_id):
        raise PermissionDenied("Only an active client-company member can revoke this access.")
    if not has_permission(revoked_by, "access.role.manage", client_id):
        raise PermissionDenied("You do not have permission to manage external access.")

    with transaction.atomic():
        grant = AccountantAccessGrant.objects.select_for_update().get(id=grant_id)
        if grant.status == AccountantAccessGrant.Status.REVOKED:
            return grant
        grant.status = AccountantAccessGrant.Status.REVOKED
        grant.revoked_by = revoked_by
        grant.revoked_at = timezone.now()
        grant.save()
    return grant



def revoke_accountant_invitation(*, invitation_id, revoked_by):
    firm_id = get_current_org_id()
    if firm_id is None:
        raise PermissionDenied("Select your accounting firm's organization first.")
    if not _has_active_membership(revoked_by, firm_id):
        raise PermissionDenied("Only an active firm member can revoke this invitation.")
    if not has_permission(revoked_by, "access.user.invite", firm_id):
        raise PermissionDenied("You do not have permission to manage invitations.")

    with transaction.atomic():
        invitation = Invitation.objects.select_for_update().get(id=invitation_id)
        if invitation.status != Invitation.Status.PENDING:
            raise ValidationError({"invitation": "Only pending invitations can be revoked."})
        if invitation.expires_at <= timezone.now():
            invitation.status = Invitation.Status.EXPIRED
        else:
            invitation.status = Invitation.Status.REVOKED
            invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["status", "revoked_at", "updated_at"])
    return invitation
