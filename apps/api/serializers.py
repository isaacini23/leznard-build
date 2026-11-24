from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.access.permissions import ACCOUNTANT_GRANTABLE_PERMISSIONS
from apps.accounts.models import User


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    full_name = serializers.CharField(max_length=200)
    company_name = serializers.CharField(max_length=200)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "An account with this email already exists. Sign in instead."
            )
        return value

    def validate(self, attrs):
        validate_password(
            attrs["password"],
            user=User(email=attrs["email"], full_name=attrs["full_name"]),
        )
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class AccountantInvitationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    requested_permissions = serializers.ListField(
        child=serializers.CharField(max_length=100),
        allow_empty=False,
    )
    expires_in_days = serializers.IntegerField(min_value=1, max_value=30, default=14)

    def validate_requested_permissions(self, value):
        unknown = sorted(set(value) - set(ACCOUNTANT_GRANTABLE_PERMISSIONS))
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported permission codes: {', '.join(unknown)}"
            )
        return sorted(set(value))


class AcceptAccountantInvitationSerializer(serializers.Serializer):
    token = serializers.CharField()
    client_organization_id = serializers.UUIDField()
    approved_permissions = serializers.ListField(
        child=serializers.CharField(max_length=100),
        allow_empty=True,
    )

    def validate_approved_permissions(self, value):
        unknown = sorted(set(value) - set(PERMISSIONS))
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported permission codes: {', '.join(unknown)}"
            )
        return sorted(set(value))



class CreateCompanySerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=200)
    country = serializers.CharField(max_length=2, default="NG")
    base_currency = serializers.CharField(max_length=3, default="NGN")
