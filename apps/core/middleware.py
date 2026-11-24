import uuid

from django.http import JsonResponse

from apps.access.models import AccountantAccessGrant
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership

ORG_HEADER = "HTTP_X_ORGANIZATION_ID"


def _deny(message, status):
    return JsonResponse({"error": {"message": message}}, status=status)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.META.get(ORG_HEADER)
        if not raw:
            return self.get_response(request)

        if not request.user.is_authenticated:
            return _deny("Authentication required.", 401)

        try:
            org_id = uuid.UUID(raw)
        except (TypeError, ValueError, AttributeError):
            return _deny("You do not have access to this organization.", 403)

        # The context is established before checking the grant because grants
        # are tenant-owned by the client organization and protected by RLS.
        with organization_context(org_id):
            is_member = Membership.objects.filter(
                user=request.user,
                organization_id=org_id,
                status=Membership.Status.ACTIVE,
            ).exists()

            has_accountant_grant = False
            if not is_member:
                has_accountant_grant = AccountantAccessGrant.objects.filter(
                    organization_id=org_id,
                    status=AccountantAccessGrant.Status.ACTIVE,
                    accountant_organization__memberships__user=request.user,
                    accountant_organization__memberships__status=Membership.Status.ACTIVE,
                ).exists()

            if not (is_member or has_accountant_grant):
                return _deny("You do not have access to this organization.", 403)

            return self.get_response(request)
