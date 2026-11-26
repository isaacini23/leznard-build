from rest_framework import serializers

from apps.accounting.models import Account
from apps.contacts.models import Contact
from apps.sales.models import PurchaseBill, SalesInvoice

from .models import Payment, PaymentAllocation


class PaymentAllocationSerializer(serializers.ModelSerializer):
    sales_invoice = serializers.PrimaryKeyRelatedField(
        queryset=SalesInvoice.unscoped.none(), required=False, allow_null=True
    )
    purchase_bill = serializers.PrimaryKeyRelatedField(
        queryset=PurchaseBill.unscoped.none(), required=False, allow_null=True
    )

    class Meta:
        model = PaymentAllocation
        fields = ("id", "sales_invoice", "purchase_bill", "amount")
        read_only_fields = ("id",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["sales_invoice"].queryset = SalesInvoice.unscoped.filter(
                organization_id=org_id, status=SalesInvoice.Status.POSTED
            )
            self.fields["purchase_bill"].queryset = PurchaseBill.unscoped.filter(
                organization_id=org_id, status=PurchaseBill.Status.POSTED
            )


class PaymentSerializer(serializers.ModelSerializer):
    contact = serializers.PrimaryKeyRelatedField(queryset=Contact.unscoped.none())
    account = serializers.PrimaryKeyRelatedField(queryset=Account.unscoped.none())
    allocations = PaymentAllocationSerializer(many=True, required=False)
    allocated_amount = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    unallocated_amount = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)

    class Meta:
        model = Payment
        fields = (
            "id", "payment_type", "payment_date", "contact", "account", "amount",
            "currency", "exchange_rate", "method", "reference", "notes", "status",
            "allocations", "allocated_amount", "unallocated_amount", "journal_entry",
            "posted_at", "created_at", "updated_at",
        )
        read_only_fields = (
            "id", "status", "journal_entry", "posted_at", "created_at", "updated_at",
            "allocated_amount", "unallocated_amount",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["contact"].queryset = Contact.unscoped.filter(
                organization_id=org_id, is_active=True
            )
            self.fields["account"].queryset = Account.unscoped.filter(
                organization_id=org_id, is_active=True,
                category=Account.Category.ASSET,
                account_type__in=[Account.AccountType.CASH, Account.AccountType.BANK],
            )

    def create(self, validated_data):
        allocations = validated_data.pop("allocations", [])
        payment = Payment.objects.create(**validated_data)
        for allocation in allocations:
            PaymentAllocation.objects.create(payment=payment, **allocation)
        return payment
