from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.tenancy import get_current_org_id

from .models import Contact, ContactAddress, ContactRole
from .pagination import ContactPagination
from .permissions import ContactAccessPermission
from .serializers import (
    ContactAddressSerializer,
    ContactRoleSerializer,
    ContactSerializer,
)


def _save_serializer(serializer):
    try:
        return serializer.save()
    except DjangoValidationError as exc:
        detail = getattr(exc, "message_dict", None) or getattr(
            exc, "messages", [str(exc)]
        )
        raise ValidationError(detail)
    except IntegrityError:
        raise ValidationError(
            {"detail": "A conflicting contact role or address already exists."}
        )


def _save_model(instance):
    try:
        instance.save()
        return instance
    except DjangoValidationError as exc:
        detail = getattr(exc, "message_dict", None) or getattr(
            exc, "messages", [str(exc)]
        )
        raise ValidationError(detail)
    except IntegrityError:
        raise ValidationError(
            {"detail": "A conflicting contact role or address already exists."}
        )


class ContactListCreateView(APIView):
    permission_classes = (ContactAccessPermission,)
    pagination_class = ContactPagination

    def get_queryset(self, request):
        queryset = Contact.objects.all().prefetch_related("roles", "addresses")

        contact_type = request.query_params.get("contact_type")
        if contact_type:
            valid_types = {choice for choice, _ in Contact.ContactType.choices}
            if contact_type not in valid_types:
                raise ValidationError({"contact_type": "Choose a supported contact type."})
            queryset = queryset.filter(contact_type=contact_type)

        active = request.query_params.get("is_active")
        if active is not None:
            if active.lower() not in {"true", "false"}:
                raise ValidationError({"is_active": "Use true or false."})
            queryset = queryset.filter(is_active=active.lower() == "true")

        role = request.query_params.get("role")
        if role:
            valid_roles = {choice for choice, _ in ContactRole.Role.choices}
            if role not in valid_roles:
                raise ValidationError({"role": "Choose a supported contact role."})
            queryset = queryset.filter(roles__role=role).distinct()

        city = request.query_params.get("city")
        if city:
            queryset = queryset.filter(addresses__city__icontains=city).distinct()

        state_name = request.query_params.get("state")
        if state_name:
            queryset = queryset.filter(addresses__state__icontains=state_name).distinct()

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(display_name__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(legal_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(tax_id__icontains=search)
            ).distinct()

        return queryset.order_by("display_name", "id")

    def get(self, request):
        queryset = self.get_queryset(request)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ContactSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = ContactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = _save_serializer(serializer)
        return Response(
            ContactSerializer(contact).data,
            status=status.HTTP_201_CREATED,
        )


class ContactDetailView(APIView):
    permission_classes = (ContactAccessPermission,)

    def get_object(self, contact_id):
        return get_object_or_404(
            Contact.objects.prefetch_related("roles", "addresses"),
            pk=contact_id,
        )

    def get(self, request, contact_id):
        return Response(ContactSerializer(self.get_object(contact_id)).data)

    def put(self, request, contact_id):
        return self._update(request, contact_id, partial=False)

    def patch(self, request, contact_id):
        return self._update(request, contact_id, partial=True)

    def _update(self, request, contact_id, partial):
        contact = self.get_object(contact_id)
        serializer = ContactSerializer(
            contact,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        contact = _save_serializer(serializer)
        return Response(ContactSerializer(contact).data)

    def delete(self, request, contact_id):
        """Soft-delete a contact so historical business records remain intact."""
        contact = self.get_object(contact_id)
        if contact.is_active:
            contact.is_active = False
            _save_model(contact)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _get_contact(contact_id):
    return get_object_or_404(Contact.objects.all(), pk=contact_id)


class ContactRoleListCreateView(APIView):
    permission_classes = (ContactAccessPermission,)

    def get(self, request, contact_id):
        contact = _get_contact(contact_id)
        roles = ContactRole.objects.filter(contact=contact).order_by("role")
        return Response(ContactRoleSerializer(roles, many=True).data)

    def post(self, request, contact_id):
        contact = _get_contact(contact_id)
        serializer = ContactRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = ContactRole(
            contact=contact,
            role=serializer.validated_data["role"],
            organization_id=get_current_org_id(),
        )
        _save_model(role)
        return Response(
            ContactRoleSerializer(role).data,
            status=status.HTTP_201_CREATED,
        )


class ContactRoleDetailView(APIView):
    permission_classes = (ContactAccessPermission,)

    def delete(self, request, contact_id, role_id):
        contact = _get_contact(contact_id)
        role = get_object_or_404(
            ContactRole.objects.filter(contact=contact),
            pk=role_id,
        )
        role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactAddressListCreateView(APIView):
    permission_classes = (ContactAccessPermission,)

    def get(self, request, contact_id):
        contact = _get_contact(contact_id)
        addresses = ContactAddress.objects.filter(contact=contact).order_by(
            "-is_default", "address_type", "created_at"
        )
        return Response(ContactAddressSerializer(addresses, many=True).data)

    def post(self, request, contact_id):
        contact = _get_contact(contact_id)
        serializer = ContactAddressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        address = ContactAddress(
            contact=contact,
            organization_id=get_current_org_id(),
            **serializer.validated_data,
        )
        _save_model(address)
        return Response(
            ContactAddressSerializer(address).data,
            status=status.HTTP_201_CREATED,
        )


class ContactAddressDetailView(APIView):
    permission_classes = (ContactAccessPermission,)

    def get_object(self, contact_id, address_id):
        contact = _get_contact(contact_id)
        return get_object_or_404(
            ContactAddress.objects.filter(contact=contact),
            pk=address_id,
        )

    def patch(self, request, contact_id, address_id):
        address = self.get_object(contact_id, address_id)
        serializer = ContactAddressSerializer(
            address,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        address = _save_serializer(serializer)
        return Response(ContactAddressSerializer(address).data)

    def delete(self, request, contact_id, address_id):
        address = self.get_object(contact_id, address_id)
        address.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
