import hashlib
import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.tenancy import get_current_org_id, organization_context
from apps.organizations.models import Membership

from .models import AccountantAccessGrant, Invitation, Role, RoleAssignment
from .permissions import ALL, ACCOUNTANT_GRANTABLE_PERMISSIONS, DEFAULT_ROLES


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

    # External accountants need both a client-owned grant and an active
    # role in their own firm. Effective permissions are the intersection of
    # the client's grant and the firm's role, never the union.
    return _accountant_permissions(user, active_org_id)


def _accountant_permissions(user, client_organization_id):
    with organization_context(client_organization_id):
        grants = list(
            AccountantAccessGrant.objects.filter(
                organization_id=client_organization_id,
                accountant_organization__memberships__user=user,
                accountant_organization__memberships__status=Membership.Status.ACTIVE,
                status=AccountantAccessGrant.Status.ACTIVE,
            ).distinct()
        )

    effective = set()
    for grant in grants:
        firm_id = grant.accountant_organization_id
        with organization_context(firm_id):
            assignment = (
                RoleAssignment.objects.filter(
                    user=user,
                    organization_id=firm_id,
                    role__organization_id=firm_id,
                )
                .select_related("role")
                .first()
            )
        if assignment is None:
            continue
        firm_permissions = set(assignment.role.permissions)
        if ALL in firm_permissions:
            effective.update(grant.permissions)
        else:
            effective.update(set(grant.permissions).intersection(firm_permissions))
    return effective


def has_accountant_access(user, client_organization_id):
    """Whether a firm user has a live client grant and a firm role."""
    if not user.is_authenticated:
        return False
    return bool(_accountant_permissions(user, client_organization_id))


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
    requested_permissions = set(requested_permissions)
    if not requested_permissions or not requested_permissions.issubset(
        set(ACCOUNTANT_GRANTABLE_PERMISSIONS)
    ):
        raise ValidationError(
            {"requested_permissions": "Choose one or more supported accountant permissions."}
        )

    secret = secrets.token_urlsafe(32)
    token = f"{firm_id}.{secret}"
    token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()

    normalized_email = email.strip().lower()
    try:
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
                requested_permissions=sorted(requested_permissions),
                invited_by=invited_by,
                token_hash=token_hash,
                expires_at=now + timedelta(days=expires_in_days),
            )
    except IntegrityError:
        # The conditional unique constraint is the final guard against two
        # concurrent requests creating pending invitations for one recipient.
        raise ValidationError({"email": "A pending invitation already exists for this email."})
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
                if not approved or not approved.issubset(
                    set(ACCOUNTANT_GRANTABLE_PERMISSIONS)
                ):
                    raise ValidationError(
                        {"approved_permissions": "Select one or more supported accountant permissions."}
                    )
                if not approved.issubset(requested):
                    raise ValidationError(
                        {"approved_permissions": "You can approve only permissions requested by the firm."}
                    )

                with organization_context(client_id):
                    grant, created = AccountantAccessGrant.objects.get_or_create(
                        accountant_organization_id=firm_id,
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


# ---- staff (member) invitations ----------------------------------------------


def _token_parts(token):
    try:
        org_text, secret = token.split(".", 1)
        return uuid.UUID(org_text), secret
    except (AttributeError, TypeError, ValueError):
        raise ValidationError({"token": "This invitation link is invalid."})


def create_member_invitation(*, invited_by, email, role_id, expires_in_days=7):
    """Invite someone to the active company with one of its roles.

    Returns (invitation, raw_token). Only a hash of the secret is stored.
    """
    org_id = get_current_org_id()
    if org_id is None:
        raise PermissionDenied("Select a company first.")
    if not _has_active_membership(invited_by, org_id):
        raise PermissionDenied("You must be an active member of this company.")
    granted = get_permissions(invited_by, org_id)
    if ALL not in granted and "access.user.invite" not in granted:
        raise PermissionDenied("You do not have permission to invite people.")
    if not 1 <= expires_in_days <= 30:
        raise ValidationError({"expires_in_days": "Invitation expiry must be between 1 and 30 days."})

    role = Role.objects.filter(id=role_id).first()
    if role is None:
        raise ValidationError({"role": "Choose one of this company's roles."})
    # Nobody can hand out more access than they hold themselves.
    if ALL not in granted and not set(role.permissions) <= granted:
        raise PermissionDenied("You cannot invite someone to a role with more access than your own.")

    normalized_email = email.strip().lower()
    if Membership.objects.filter(
        organization_id=org_id,
        user__email=normalized_email,
        status=Membership.Status.ACTIVE,
    ).exists():
        raise ValidationError({"email": "This person is already a member of the company."})

    secret = secrets.token_urlsafe(32)
    token = f"{org_id}.{secret}"
    token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    try:
        with transaction.atomic():
            now = timezone.now()
            previous = Invitation.objects.select_for_update().filter(
                email=normalized_email,
                kind=Invitation.Kind.MEMBER,
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
                kind=Invitation.Kind.MEMBER,
                role=role,
                invited_by=invited_by,
                token_hash=token_hash,
                expires_at=now + timedelta(days=expires_in_days),
            )
    except IntegrityError:
        raise ValidationError({"email": "A pending invitation already exists for this email."})
    return invitation, token


def accept_member_invitation(*, token, password, full_name=""):
    """Turn a valid staff invitation into a membership and role.

    A new person creates their account here. Someone who already has an account
    proves it with their password. Returns (user, organization).
    """
    org_id, secret = _token_parts(token)
    token_hash = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    invalid = ValidationError({"token": "This invitation is invalid or has expired."})
    expired = False
    with transaction.atomic(), organization_context(org_id):
        invitation = (
            Invitation.objects.select_for_update(of=("self",))
            .select_related("role", "organization")
            .filter(token_hash=token_hash, kind=Invitation.Kind.MEMBER)
            .first()
        )
        if invitation is None or invitation.status != Invitation.Status.PENDING:
            raise invalid
        if invitation.expires_at <= timezone.now():
            invitation.status = Invitation.Status.EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
            expired = True
        else:
            organization = invitation.organization
            user = User.objects.filter(email=invitation.email).first()
            if user is not None:
                if not user.check_password(password):
                    raise ValidationError({"password": "That password does not match the existing account."})
            else:
                if not full_name.strip():
                    raise ValidationError({"full_name": "Please enter your full name."})
                validate_password(password, user=User(email=invitation.email, full_name=full_name))
                user = User.objects.create_user(
                    email=invitation.email, password=password, full_name=full_name.strip()
                )

            membership, created = Membership.objects.get_or_create(
                user=user, organization=organization
            )
            if not created:
                if membership.status == Membership.Status.SUSPENDED:
                    raise PermissionDenied("This account is suspended in the company.")
                if membership.status == Membership.Status.ACTIVE and RoleAssignment.objects.filter(
                    user=user
                ).exists():
                    raise ValidationError({"email": "This person is already a member of the company."})
                membership.status = Membership.Status.ACTIVE
                membership.save(update_fields=["status"])

            RoleAssignment.objects.update_or_create(user=user, defaults={"role": invitation.role})
            invitation.status = Invitation.Status.ACCEPTED
            # accepted_organization is only for accountant-client invitations;
            # a staff invitation's company is its own organization.
            invitation.accepted_by = user
            invitation.accepted_at = timezone.now()
            invitation.save(
                update_fields=["status", "accepted_by", "accepted_at", "updated_at"]
            )
    if expired:
        raise ValidationError({"token": "This invitation has expired."})
    return user, organization
