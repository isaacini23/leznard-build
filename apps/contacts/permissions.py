from rest_framework.permissions import BasePermission, SAFE_METHODS

from apps.access.services import has_permission
from apps.core.tenancy import get_current_org_id


class ContactAccessPermission(BasePermission):
    """Apply separate read and write permissions inside the selected tenant."""

    message = "You do not have permission to access contacts in this organization."

    def has_permission(self, request, view):
        organization_id = get_current_org_id()
        if organization_id is None:
            return False

        code = (
            "contacts.contact.view"
            if request.method in SAFE_METHODS
            else "contacts.contact.manage"
        )
        return has_permission(
            request.user,
            code,
            organization_id=organization_id,
        )
