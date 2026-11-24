from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.access.models import AccountantAccessGrant, Invitation, Role
from apps.access.services import (
    accept_accountant_invitation,
    create_accountant_invitation,
    get_permissions,
    revoke_accountant_access,
    revoke_accountant_invitation,
)
from apps.core.tenancy import get_current_org_id
from apps.organizations.models import Membership

from .registration import register_company
from .serializers import (
    AcceptAccountantInvitationSerializer,
    AccountantInvitationSerializer,
    LoginSerializer,
    RegisterSerializer,
)


def _service_error_response(exc):
    if isinstance(exc, PermissionDenied):
        return Response({"error": {"message": str(exc)}}, status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, DjangoValidationError):
        detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
        return Response({"error": {"details": detail}}, status=status.HTTP_400_BAD_REQUEST)
    raise exc


class MeView(APIView):
    def get(self, request):
        memberships = request.user.memberships.filter(
            status=Membership.Status.ACTIVE
        ).select_related("organization")
        active = get_current_org_id()
        return Response(
            {
                "email": request.user.email,
                "full_name": request.user.full_name,
                "organizations": [
                    {"id": str(m.organization_id), "name": m.organization.name}
                    for m in memberships
                ],
                "active_organization": str(active) if active else None,
                "permissions": sorted(
                    get_permissions(request.user, organization_id=active)
                ),
            }
        )


class RoleListView(APIView):
    permission_classes = [IsAuthenticated, require("access.role.manage")]

    def get(self, request):
        roles = Role.objects.order_by("name")
        return Response(
            [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "description": r.description,
                    "permissions": r.permissions,
                }
                for r in roles
            ]
        )


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, organization = register_company(**serializer.validated_data)
        login(request._request, user)
        return Response(
            {
                "user": {"id": str(user.id), "email": user.email},
                "organization": {"id": str(organization.id), "name": organization.name},
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request._request,
            username=serializer.validated_data["email"].lower(),
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"error": {"message": "Invalid email or password."}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        login(request._request, user)
        memberships = user.memberships.filter(
            status=Membership.Status.ACTIVE
        ).select_related("organization")
        return Response(
            {
                "user": {"id": str(user.id), "email": user.email},
                "organizations": [
                    {"id": str(m.organization_id), "name": m.organization.name}
                    for m in memberships
                ],
            }
        )


class LogoutView(APIView):
    def post(self, request):
        logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CreateAccountantInvitationView(APIView):
    permission_classes = [IsAuthenticated, require("access.user.invite")]

    def post(self, request):
        serializer = AccountantInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            invitation, token = create_accountant_invitation(
                invited_by=request.user,
                **serializer.validated_data,
            )
        except (PermissionDenied, DjangoValidationError) as exc:
            return _service_error_response(exc)
        return Response(
            {
                "invitation_id": str(invitation.id),
                "email": invitation.email,
                "expires_at": invitation.expires_at,
                # The raw token is returned once; only its hash is stored.
                "token": token,
            },
            status=status.HTTP_201_CREATED,
        )


class AcceptAccountantInvitationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AcceptAccountantInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            grant = accept_accountant_invitation(
                accepted_by=request.user,
                **serializer.validated_data,
            )
        except (PermissionDenied, DjangoValidationError) as exc:
            return _service_error_response(exc)
        return Response(
            {
                "grant_id": str(grant.id),
                "client_organization_id": str(grant.organization_id),
                "accountant_organization_id": str(grant.accountant_organization_id),
                "permissions": grant.permissions,
                "status": grant.status,
            },
            status=status.HTTP_201_CREATED,
        )


class RevokeAccountantAccessView(APIView):
    permission_classes = [IsAuthenticated, require("access.role.manage")]

    def post(self, request, grant_id):
        try:
            grant = revoke_accountant_access(
                grant_id=grant_id,
                revoked_by=request.user,
            )
        except (PermissionDenied, DjangoValidationError) as exc:
            return _service_error_response(exc)
        except AccountantAccessGrant.DoesNotExist:
            return Response(
                {"error": {"message": "Access grant not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {"grant_id": str(grant.id), "status": grant.status},
            status=status.HTTP_200_OK,
        )



class RevokeAccountantInvitationView(APIView):
    permission_classes = [IsAuthenticated, require("access.user.invite")]

    def post(self, request, invitation_id):
        try:
            invitation = revoke_accountant_invitation(
                invitation_id=invitation_id,
                revoked_by=request.user,
            )
        except (PermissionDenied, DjangoValidationError) as exc:
            return _service_error_response(exc)
        except Invitation.DoesNotExist:
            return Response(
                {"error": {"message": "Invitation not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {"invitation_id": str(invitation.id), "status": invitation.status},
            status=status.HTTP_200_OK,
        )
