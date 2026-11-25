from rest_framework import serializers

from .models import Contact, ContactAddress, ContactRole


class ContactRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactRole
        fields = ("id", "role", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class ContactAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContactAddress
        fields = (
            "id",
            "address_type",
            "street_address",
            "address_line_2",
            "city",
            "state",
            "country",
            "postal_code",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class ContactSerializer(serializers.ModelSerializer):
    roles = ContactRoleSerializer(many=True, read_only=True)
    addresses = ContactAddressSerializer(many=True, read_only=True)

    class Meta:
        model = Contact
        fields = (
            "id",
            "contact_type",
            "display_name",
            "first_name",
            "last_name",
            "legal_name",
            "email",
            "phone",
            "website",
            "tax_id",
            "notes",
            "is_active",
            "roles",
            "addresses",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_display_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("A contact name is required.")
        return value.strip()
