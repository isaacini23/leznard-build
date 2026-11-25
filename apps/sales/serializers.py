from rest_framework import serializers

from apps.accounting.models import Account
from apps.contacts.models import Contact

from .models import PurchaseBill, PurchaseBillLine, SalesInvoice, SalesInvoiceLine


class SalesInvoiceLineSerializer(serializers.ModelSerializer):
    revenue_account = serializers.PrimaryKeyRelatedField(queryset=Account.objects.none())
    tax_account = serializers.PrimaryKeyRelatedField(queryset=Account.objects.none(), required=False, allow_null=True)

    class Meta:
        model = SalesInvoiceLine
        fields = ("id", "description", "quantity", "unit_price", "discount_amount", "tax_amount", "revenue_account", "tax_account", "net_amount")
        read_only_fields = ("id", "net_amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["revenue_account"].queryset = Account.unscoped.filter(organization_id=org_id, category=Account.Category.INCOME, is_active=True)
            self.fields["tax_account"].queryset = Account.unscoped.filter(organization_id=org_id, category=Account.Category.LIABILITY, is_active=True)


class PurchaseBillLineSerializer(serializers.ModelSerializer):
    expense_account = serializers.PrimaryKeyRelatedField(queryset=Account.objects.none())
    tax_account = serializers.PrimaryKeyRelatedField(queryset=Account.objects.none(), required=False, allow_null=True)

    class Meta:
        model = PurchaseBillLine
        fields = ("id", "description", "quantity", "unit_price", "discount_amount", "tax_amount", "expense_account", "tax_account", "net_amount")
        read_only_fields = ("id", "net_amount")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["expense_account"].queryset = Account.unscoped.filter(organization_id=org_id, category=Account.Category.EXPENSE, is_active=True)
            self.fields["tax_account"].queryset = Account.unscoped.filter(organization_id=org_id, category=Account.Category.ASSET, is_active=True)


class SalesInvoiceSerializer(serializers.ModelSerializer):
    customer = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.none())
    lines = SalesInvoiceLineSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    total = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)

    class Meta:
        model = SalesInvoice
        fields = ("id", "number", "customer", "document_date", "due_date", "currency", "exchange_rate", "notes", "status", "lines", "subtotal", "tax_total", "total", "journal_entry", "posted_at", "created_at", "updated_at")
        read_only_fields = ("id", "status", "journal_entry", "posted_at", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["customer"].queryset = Contact.unscoped.filter(organization_id=org_id, is_active=True)

    def create(self, validated_data):
        lines = validated_data.pop("lines")
        invoice = SalesInvoice.objects.create(**validated_data)
        for line in lines:
            SalesInvoiceLine.objects.create(invoice=invoice, **line)
        return invoice

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                SalesInvoiceLine.objects.create(invoice=instance, **line)
        return instance


class PurchaseBillSerializer(serializers.ModelSerializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.none())
    lines = PurchaseBillLineSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    tax_total = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)
    total = serializers.DecimalField(max_digits=20, decimal_places=4, read_only=True)

    class Meta:
        model = PurchaseBill
        fields = ("id", "number", "supplier", "supplier_reference", "document_date", "due_date", "currency", "exchange_rate", "notes", "status", "lines", "subtotal", "tax_total", "total", "journal_entry", "posted_at", "created_at", "updated_at")
        read_only_fields = ("id", "status", "journal_entry", "posted_at", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        org_id = self.context.get("organization_id")
        if org_id:
            self.fields["supplier"].queryset = Contact.unscoped.filter(organization_id=org_id, is_active=True)

    def create(self, validated_data):
        lines = validated_data.pop("lines")
        bill = PurchaseBill.objects.create(**validated_data)
        for line in lines:
            PurchaseBillLine.objects.create(bill=bill, **line)
        return bill

    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if lines is not None:
            instance.lines.all().delete()
            for line in lines:
                PurchaseBillLine.objects.create(bill=instance, **line)
        return instance
