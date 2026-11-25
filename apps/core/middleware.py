import uuid

from django.http import JsonResponse

from apps.access.services import (
    has_accountant_access,
    has_permission as user_has_permission,
)
from apps.core.tenancy import organization_context
from apps.organizations.models import Membership

ORG_HEADER = "HTTP_X_ORGANIZATION_ID"


def _deny(message, status):
    return JsonResponse({"error": {"message": message}}, status=status)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _has_access(self, user, organization_id):
        is_member = Membership.objects.filter(
            user=user,
            organization_id=organization_id,
            status=Membership.Status.ACTIVE,
        ).exists()
        return is_member or has_accountant_access(user, organization_id)

    def __call__(self, request):
        raw = request.META.get(ORG_HEADER)
        is_admin_request = request.path.startswith("/admin/")

        # The admin accepts an explicit organization_id once, then keeps that
        # selection in the authenticated session. Membership/grant validation
        # still happens below, so this parameter cannot grant access by itself.
        if not raw and is_admin_request and request.user.is_authenticated:
            raw = (
                request.GET.get("organization_id")
                or request.session.get("active_organization_id")
            )
            if not raw:
                memberships = list(
                    Membership.objects.filter(
                        user=request.user,
                        status=Membership.Status.ACTIVE,
                    ).values_list("organization_id", flat=True)[:2]
                )
                if len(memberships) == 1:
                    raw = str(memberships[0])
                elif len(memberships) > 1:
                    return _deny(
                        "Choose an organization by adding ?organization_id=<company-id> to this admin URL.",
                        400,
                    )
                else:
                    return _deny(
                        "An active organization membership is required to access company data.",
                        403,
                    )

        if not raw:
            return self.get_response(request)

        if not request.user.is_authenticated:
            return _deny("Authentication required.", 401)

        try:
            org_id = uuid.UUID(str(raw))
        except (TypeError, ValueError, AttributeError):
            return _deny("You do not have access to this organization.", 403)

        # The context is established before checking the grant because grants
        # are tenant-owned by the client organization and protected by RLS.
        with organization_context(org_id):
            if not self._has_access(request.user, org_id):
                return _deny("You do not have access to this organization.", 403)

            if is_admin_request and request.path.startswith("/admin/contacts/"):
                if not request.user.is_superuser:
                    code = (
                        "contacts.contact.view"
                        if request.method in {"GET", "HEAD", "OPTIONS"}
                        else "contacts.contact.manage"
                    )
                    if not user_has_permission(request.user, code, org_id):
                        return _deny(
                            "You do not have permission to manage contacts in this organization.",
                            403,
                        )

            if hasattr(request, "session"):
                request.session["active_organization_id"] = str(org_id)

            return self.get_response(request)
