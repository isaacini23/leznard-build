from rest_framework.permissions import BasePermission

from apps.core.tenancy import get_current_org_id

from .services import has_permission as user_has_permission


def require(code):
    class RequirePermission(BasePermission):
        message = "You do not have permission to do this."

        def has_permission(self, request, view):
            return user_has_permission(
                request.user,
                code,
                organization_id=get_current_org_id(),
            )

    return RequirePermission
