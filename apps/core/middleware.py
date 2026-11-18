import uuid

from django.http import JsonResponse

from apps.organizations.models import Membership

from .tenancy import organization_context

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
        except ValueError:
            return _deny("You do not have access to this organization.", 403)

        is_member = Membership.objects.filter(
            user=request.user,
            organization_id=org_id,
            status=Membership.Status.ACTIVE,
        ).exists()
        if not is_member:
            return _deny("You do not have access to this organization.", 403)

        with organization_context(org_id):
            return self.get_response(request)