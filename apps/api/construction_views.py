"""Construction projects API."""
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.construction import reports, services
from apps.construction.models import (
    BudgetLine,
    Project,
    ProjectCost,
    ProjectInvoice,
    Subcontract,
    SubcontractClaim,
    Variation,
)
from apps.contacts.models import Contact


def _error(exc):
    detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
    return Response({"error": {"details": detail}}, status=400)


def _guard(call):
    try:
        return call(), None
    except DjangoValidationError as exc:
        return None, _error(exc)
    except IntegrityError:
        return None, Response({"error": {"details": ["A record with these details already exists."]}}, status=400)


def _date(value, field, required=False):
    if not value:
        if required:
            raise ValidationError({field: "This date is required."})
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError({field: "Use a date like 2027-01-31."})


def _truthy(value, default=True):
    if value is None:
        return default
    return value in (True, "true", "1", 1)


class _Base(APIView):
    """Reads need construction.project.view; plain edits need .manage; money actions set `money`."""

    money = False

    def get_permissions(self):
        if self.request.method == "GET":
            code = "construction.project.view"
        else:
            code = "construction.payment.record" if self.money else "construction.project.manage"
        return [IsAuthenticated(), require(code)()]


# ---- serializers ----------------------------------------------------------------------------


class ProjectSerializer(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.none())
    client_name = serializers.CharField(source="client.display_name", read_only=True)

    class Meta:
        model = Project
        fields = ["id", "code", "name", "client", "client_name", "status", "start_date", "end_date",
                  "site_address", "contract_value", "notes"]
        read_only_fields = ["id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Contact.objects.all()


class BudgetLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetLine
        fields = ["id", "project", "cost_type", "description", "budget_amount"]
        read_only_fields = ["id", "project"]


class VariationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Variation
        fields = ["id", "project", "description", "amount", "status", "decided_on"]
        read_only_fields = ["id", "project", "status", "decided_on"]


class SubcontractSerializer(serializers.ModelSerializer):
    subcontractor = serializers.PrimaryKeyRelatedField(queryset=Contact.objects.none())
    budget_line = serializers.PrimaryKeyRelatedField(queryset=BudgetLine.objects.none(), allow_null=True, required=False)
    subcontractor_name = serializers.CharField(source="subcontractor.display_name", read_only=True)
    claimed = serializers.SerializerMethodField()

    class Meta:
        model = Subcontract
        fields = ["id", "project", "subcontractor", "subcontractor_name", "description", "contract_sum",
                  "retention_percent", "budget_line", "status", "claimed"]
        read_only_fields = ["id", "project", "claimed"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subcontractor"].queryset = Contact.objects.all()
        self.fields["budget_line"].queryset = BudgetLine.objects.all()

    def get_claimed(self, obj):
        return str(services._claimed(obj))


class ClaimSerializer(serializers.ModelSerializer):
    net_payable = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)
    retention_held = serializers.DecimalField(max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = SubcontractClaim
        fields = ["id", "subcontract", "number", "claim_date", "description", "gross_amount",
                  "retention_amount", "net_payable", "retention_released", "retention_held",
                  "status", "purchase_bill"]


# ---- projects --------------------------------------------------------------------------------------


class ProjectListView(_Base):
    def get(self, request):
        qs = Project.objects.select_related("client")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(ProjectSerializer(qs, many=True).data)

    def post(self, request):
        serializer = ProjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project, error = _guard(lambda: serializer.save())
        if error:
            return error
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)


class ProjectDetailView(_Base):
    def get(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        return Response(ProjectSerializer(project).data) if project else Response(status=404)

    def patch(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(status=404)
        serializer = ProjectSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        project, error = _guard(lambda: serializer.save())
        return error or Response(ProjectSerializer(project).data)


class ProjectSummaryView(_Base):
    def get(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        return Response(reports.project_summary(project)) if project else Response(status=404)


class _ProjectChildList(_Base):
    serializer = None
    model = None

    def project(self, pk):
        return Project.objects.filter(pk=pk).first()

    def get(self, request, pk):
        project = self.project(pk)
        if project is None:
            return Response(status=404)
        return Response(self.serializer(self.model.objects.filter(project=project), many=True).data)

    def create(self, request, project):
        serializer = self.serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj, error = _guard(lambda: serializer.save(project=project))
        return error or Response(self.serializer(obj).data, status=status.HTTP_201_CREATED)

    def post(self, request, pk):
        project = self.project(pk)
        return self.create(request, project) if project else Response(status=404)


class BudgetLinesView(_ProjectChildList):
    serializer, model = BudgetLineSerializer, BudgetLine


class BudgetLineDetailView(_Base):
    def patch(self, request, pk):
        line = BudgetLine.objects.filter(pk=pk).first()
        if line is None:
            return Response(status=404)
        serializer = BudgetLineSerializer(line, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        line, error = _guard(lambda: serializer.save())
        return error or Response(BudgetLineSerializer(line).data)


class VariationsView(_ProjectChildList):
    serializer, model = VariationSerializer, Variation


class _VariationDecision(_Base):
    approve = True

    def post(self, request, pk):
        if not Variation.objects.filter(pk=pk).exists():
            return Response(status=404)
        variation, error = _guard(lambda: services.decide_variation(
            pk, approve=self.approve, decided_on=_date(request.data.get("date"), "date")))
        return error or Response(VariationSerializer(variation).data)


class VariationApproveView(_VariationDecision):
    approve = True


class VariationRejectView(_VariationDecision):
    approve = False


class ProjectCostsView(_Base):
    """Charge an existing purchase bill to the project (optionally to one budget line)."""

    def get(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(status=404)
        rows = ProjectCost.objects.filter(project=project).select_related("purchase_bill", "budget_line")
        return Response([{
            "id": str(c.pk), "purchase_bill": str(c.purchase_bill_id), "number": c.purchase_bill.number,
            "status": c.purchase_bill.status, "amount": str(c.purchase_bill.subtotal),
            "budget_line": str(c.budget_line_id) if c.budget_line_id else None,
        } for c in rows])

    def post(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(status=404)
        cost, error = _guard(lambda: services.link_bill_to_project(
            project, request.data.get("purchase_bill"), request.data.get("budget_line") or None))
        if error:
            return error
        return Response({"id": str(cost.pk), "purchase_bill": str(cost.purchase_bill_id)}, status=201)


class ProjectInvoicesView(_Base):
    money = True

    def get(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(status=404)
        rows = ProjectInvoice.objects.filter(project=project).select_related("invoice")
        return Response([{
            "id": str(r.pk), "invoice": str(r.invoice_id), "number": r.invoice.number,
            "status": r.invoice.status, "amount": str(r.invoice.subtotal), "total": str(r.invoice.total),
            "description": r.description,
        } for r in rows])

    def post(self, request, pk):
        project = Project.objects.filter(pk=pk).first()
        if project is None:
            return Response(status=404)
        data = request.data
        link, error = _guard(lambda: services.create_progress_invoice(
            project, amount=data.get("amount"), description=str(data.get("description") or ""),
            invoice_date=_date(data.get("invoice_date"), "invoice_date"),
            due_days=int(data.get("due_days") or 14), revenue_account_id=data.get("revenue_account") or None,
            post=_truthy(data.get("post")), posted_by=request.user))
        if error:
            return error
        return Response({"id": str(link.pk), "invoice": str(link.invoice_id), "number": link.invoice.number,
                         "status": link.invoice.status}, status=201)


# ---- subcontracts and claims ---------------------------------------------------------------------------


class SubcontractsView(_ProjectChildList):
    serializer, model = SubcontractSerializer, Subcontract


class SubcontractDetailView(_Base):
    def get(self, request, pk):
        sub = Subcontract.objects.filter(pk=pk).first()
        return Response(SubcontractSerializer(sub).data) if sub else Response(status=404)

    def patch(self, request, pk):
        sub = Subcontract.objects.filter(pk=pk).first()
        if sub is None:
            return Response(status=404)
        if SubcontractClaim.objects.filter(subcontract=sub).exclude(status="cancelled").exists() and \
                set(request.data) - {"status", "description"}:
            return Response({"error": {"details": ["Only the status and description can change once claims exist."]}}, status=400)
        serializer = SubcontractSerializer(sub, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        sub, error = _guard(lambda: serializer.save())
        return error or Response(SubcontractSerializer(sub).data)


class ClaimsView(_Base):
    money = True

    def get(self, request, pk):
        sub = Subcontract.objects.filter(pk=pk).first()
        if sub is None:
            return Response(status=404)
        return Response(ClaimSerializer(SubcontractClaim.objects.filter(subcontract=sub), many=True).data)

    def post(self, request, pk):
        if not Subcontract.objects.filter(pk=pk).exists():
            return Response(status=404)
        data = request.data
        claim, error = _guard(lambda: services.create_claim(
            pk, gross_amount=data.get("gross_amount"), claim_date=_date(data.get("claim_date"), "claim_date"),
            description=str(data.get("description") or "")))
        return error or Response(ClaimSerializer(claim).data, status=201)


class _ClaimAction(_Base):
    money = True

    def run(self, request, pk):
        raise NotImplementedError

    def post(self, request, pk):
        if not SubcontractClaim.objects.filter(pk=pk).exists():
            return Response(status=404)
        claim, error = _guard(lambda: self.run(request, pk))
        return error or Response(ClaimSerializer(claim).data)


class ClaimCertifyView(_ClaimAction):
    def run(self, request, pk):
        d = request.data
        return services.certify_claim(
            pk, expense_account_id=d.get("expense_account") or None, due_days=int(d.get("due_days") or 30),
            post=_truthy(d.get("post")), posted_by=request.user)


class ClaimCancelView(_ClaimAction):
    def run(self, request, pk):
        return services.cancel_claim(pk)


class ClaimPayView(_ClaimAction):
    def run(self, request, pk):
        d = request.data
        return services.pay_claim(pk, account_id=d.get("account") or None, amount=d.get("amount"),
                                  payment_date=_date(d.get("payment_date"), "payment_date"), posted_by=request.user)


class ClaimReleaseRetentionView(_ClaimAction):
    def run(self, request, pk):
        d = request.data
        return services.release_retention(pk, account_id=d.get("account") or None, amount=d.get("amount"),
                                          payment_date=_date(d.get("payment_date"), "payment_date"), posted_by=request.user)
