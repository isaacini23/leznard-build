from django.contrib.auth import authenticate, login, logout
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.access.models import Role
from apps.access.services import get_permissions
from apps.core.tenancy import get_current_org_id
from apps.organizations.models import Membership

from .registration import register_company
from .serializers import LoginSerializer, RegisterSerializer


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
                "permissions": sorted(get_permissions(request.user)),
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