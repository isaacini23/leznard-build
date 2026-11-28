"""Tenant-scoped chart of accounts, journals and general-ledger API views."""
from datetime import date

from django.utils import timezone
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.accounting.models import Account, JournalEntry, JournalLine
from apps.accounting.reports import (
    balance_sheet, cash_flow_statement, comparative_balance_sheet,
    comparative_income_statement, retained_earnings,
)
from apps.accounting.services import create_journal_entry, post_journal_entry, reverse_journal_entry
from apps.core.tenancy import get_current_org_id
from apps.organizations.models import Organization


def _error(exc):
    if isinstance(exc, PermissionDenied):
        return Response({"error": {"message": str(exc)}}, status=403)
    if isinstance(exc, ValidationError):
        details = getattr(exc, "message_dict", None) or getattr(exc, "messages", [str(exc)])
        return Response({"error": {"details": details}}, status=400)
    raise exc


def _date(value, field):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError({field: "Use an ISO date in YYYY-MM-DD format."})


def _account_payload(account):
    return {
        "id": str(account.pk), "code": account.code, "name": account.name,
        "category": account.category, "account_type": account.account_type,
        "normal_balance": account.normal_balance, "description": account.description,
        "parent_id": str(account.parent_id) if account.parent_id else None,
        "is_system": account.is_system, "is_active": account.is_active,
    }


def _journal_payload(entry):
    lines = entry.lines.select_related("account").order_by("created_at", "id")
    return {
        "id": str(entry.pk), "entry_date": entry.entry_date,
        "reference": entry.reference, "description": entry.description,
        "currency": entry.currency, "base_currency": entry.base_currency,
        "exchange_rate": str(entry.exchange_rate), "status": entry.status,
        "posted_at": entry.posted_at,
        "lines": [{
            "id": str(line.pk), "account_id": str(line.account_id),
            "account_code": line.account.code, "account_name": line.account.name,
            "description": line.description, "debit": str(line.debit_amount),
            "credit": str(line.credit_amount),
            "base_debit": str(line.base_debit_amount),
            "base_credit": str(line.base_credit_amount),
        } for line in lines],
    }


class AccountListCreateView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.account.view")]

    def get(self, request):
        accounts = Account.objects.select_related("parent").all()
        category = request.query_params.get("category")
        active = request.query_params.get("active")
        if category:
            accounts = accounts.filter(category=category)
        if active in ("true", "false"):
            accounts = accounts.filter(is_active=(active == "true"))
        return Response([_account_payload(a) for a in accounts.order_by("code")])

    def post(self, request):
        from apps.access.services import has_permission
        if not has_permission(request.user, "accounting.account.manage", get_current_org_id()):
            return Response({"error": {"message": "You do not have permission to manage accounts."}}, status=403)
        data = request.data
        try:
            parent = Account.objects.get(pk=data["parent_id"]) if data.get("parent_id") else None
            account = Account(
                code=data.get("code", "").strip(), name=data.get("name", "").strip(),
                category=data.get("category"), account_type=data.get("account_type"),
                normal_balance=data.get("normal_balance"), description=data.get("description", ""),
                parent=parent, is_active=data.get("is_active", True),
            )
            account.save()
        except (KeyError, TypeError, ValueError, Account.DoesNotExist, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                return _error(exc)
            return Response({"error": {"message": "Provide valid account fields and an optional parent_id."}}, status=400)
        return Response(_account_payload(account), status=201)


class AccountDetailView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.account.view")]

    def get(self, request, pk):
        return Response(_account_payload(get_object_or_404(Account.objects, pk=pk)))

    def patch(self, request, pk):
        from apps.access.services import has_permission
        if not has_permission(request.user, "accounting.account.manage", get_current_org_id()):
            return Response({"error": {"message": "You do not have permission to manage accounts."}}, status=403)
        account = get_object_or_404(Account.objects, pk=pk)
        data = request.data
        try:
            for field in ("code", "name", "category", "account_type", "normal_balance", "description", "is_active"):
                if field in data:
                    setattr(account, field, data[field])
            if "parent_id" in data:
                account.parent = Account.objects.get(pk=data["parent_id"]) if data["parent_id"] else None
            account.save()
        except (Account.DoesNotExist, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                return _error(exc)
            return Response({"error": {"message": "Parent account not found."}}, status=400)
        return Response(_account_payload(account))


class JournalListCreateView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.journal.view")]

    def get(self, request):
        entries = JournalEntry.objects.prefetch_related("lines__account").order_by("-entry_date", "-created_at")
        status_filter = request.query_params.get("status")
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        if status_filter:
            entries = entries.filter(status=status_filter)
        try:
            if start:
                entries = entries.filter(entry_date__gte=_date(start, "start"))
            if end:
                entries = entries.filter(entry_date__lte=_date(end, "end"))
        except ValidationError as exc:
            return _error(exc)
        return Response([_journal_payload(entry) for entry in entries[:200]])

    def post(self, request):
        from apps.access.services import has_permission
        if not has_permission(request.user, "accounting.journal.manage", get_current_org_id()):
            return Response({"error": {"message": "You do not have permission to create journals."}}, status=403)
        data = request.data
        try:
            entry_date = _date(data.get("entry_date"), "entry_date")
            lines = data.get("lines")
            if not isinstance(lines, list) or len(lines) < 2:
                raise ValidationError({"lines": "Provide at least two journal lines."})
            entry = create_journal_entry(
                Organization.objects.get(pk=get_current_org_id()),
                entry_date=entry_date, description=data.get("description", "").strip(),
                reference=data.get("reference", ""), currency=data.get("currency"),
                exchange_rate=data.get("exchange_rate"), lines=lines,
            )
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                return _error(exc)
            return Response({"error": {"message": "Invalid journal payload."}}, status=400)
        return Response(_journal_payload(entry), status=201)


class JournalDetailView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.journal.view")]

    def get(self, request, pk):
        entry = get_object_or_404(JournalEntry.objects.prefetch_related("lines__account"), pk=pk)
        return Response(_journal_payload(entry))


class JournalPostView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.journal.post")]

    def post(self, request, pk):
        entry = get_object_or_404(JournalEntry.objects, pk=pk)
        try:
            posted = post_journal_entry(entry, posted_by=request.user)
        except (PermissionDenied, ValidationError) as exc:
            return _error(exc)
        return Response(_journal_payload(posted))


class JournalReverseView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.journal.post")]

    def post(self, request, pk):
        entry = get_object_or_404(JournalEntry.objects, pk=pk)
        try:
            reversal = reverse_journal_entry(
                entry, entry_date=_date(request.data.get("entry_date"), "entry_date")
                if request.data.get("entry_date") else None,
                description=request.data.get("description"), posted_by=request.user,
            )
        except (PermissionDenied, ValidationError) as exc:
            return _error(exc)
        return Response(_journal_payload(reversal), status=201)


class GeneralLedgerView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.ledger.view")]

    def get(self, request):
        account_id = request.query_params.get("account")
        if not account_id:
            return Response({"error": {"message": "The account query parameter is required."}}, status=400)
        account = get_object_or_404(Account.objects, pk=account_id)
        try:
            start = _date(request.query_params.get("start"), "start") if request.query_params.get("start") else None
            end = _date(request.query_params.get("end"), "end") if request.query_params.get("end") else timezone.localdate()
            if start and end < start:
                raise ValidationError({"end": "The end date cannot be before the start date."})
        except ValidationError as exc:
            return _error(exc)

        lines = JournalLine.objects.filter(
            account=account, entry__status__in=(JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED),
            entry__entry_date__lte=end,
        ).select_related("entry").order_by("entry__entry_date", "entry__created_at", "created_at", "id")
        if start:
            lines = lines.filter(entry__entry_date__gte=start)
        opening = Decimal("0")
        if start:
            prior = JournalLine.objects.filter(
                account=account, entry__status__in=(JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED),
                entry__entry_date__lt=start,
            ).aggregate(debit=Sum("base_debit_amount"), credit=Sum("base_credit_amount"))
            opening = (prior["debit"] or Decimal("0")) - (prior["credit"] or Decimal("0"))
        running = opening
        rows = []
        for line in lines:
            debit, credit = line.base_debit_amount, line.base_credit_amount
            running += debit - credit
            rows.append({
                "date": line.entry.entry_date, "journal_id": str(line.entry_id),
                "reference": line.entry.reference, "description": line.description or line.entry.description,
                "debit": str(debit), "credit": str(credit), "running_balance": str(running),
            })
        return Response({
            "account": _account_payload(account), "start_date": start, "end_date": end,
            "opening_balance": str(opening), "closing_balance": str(running), "entries": rows,
        })


class AccountBalancesView(APIView):
    permission_classes = [IsAuthenticated, require("accounting.ledger.view")]

    def get(self, request):
        try:
            as_of = _date(request.query_params.get("as_of") or timezone.localdate().isoformat(), "as_of")
        except ValidationError as exc:
            return _error(exc)
        totals = JournalLine.objects.filter(
            entry__status__in=(JournalEntry.Status.POSTED, JournalEntry.Status.REVERSED),
            entry__entry_date__lte=as_of,
        ).values("account_id").annotate(debit=Sum("base_debit_amount"), credit=Sum("base_credit_amount"))
        by_account = {row["account_id"]: row for row in totals}
        rows = []
        for account in Account.objects.order_by("code"):
            item = by_account.get(account.pk, {})
            debit = item.get("debit") or Decimal("0")
            credit = item.get("credit") or Decimal("0")
            rows.append({
                **_account_payload(account), "debit_total": str(debit),
                "credit_total": str(credit), "balance": str(debit - credit),
            })
        return Response({"as_of": as_of, "accounts": rows})



class BalanceSheetView(APIView):
    permission_classes = [IsAuthenticated, require("reports.report.view")]

    def get(self, request):
        try:
            as_of = _date(request.query_params.get("as_of"), "as_of")
            report = balance_sheet(as_of=as_of)
        except ValidationError as exc:
            return _error(exc)
        return Response(report)


class RetainedEarningsView(APIView):
    permission_classes = [IsAuthenticated, require("reports.report.view")]

    def get(self, request):
        try:
            as_of = _date(request.query_params.get("as_of"), "as_of")
            report = retained_earnings(as_of=as_of)
        except ValidationError as exc:
            return _error(exc)
        return Response(report)


class CashFlowStatementView(APIView):
    permission_classes = [IsAuthenticated, require("reports.report.view")]

    def get(self, request):
        try:
            start = _date(request.query_params.get("start"), "start")
            end = _date(request.query_params.get("end"), "end")
            if end < start:
                raise ValidationError({"end": "The end date cannot be before the start date."})
            report = cash_flow_statement(start_date=start, end_date=end)
        except ValidationError as exc:
            return _error(exc)
        return Response(report)


class ComparativeIncomeStatementView(APIView):
    permission_classes = [IsAuthenticated, require("reports.report.view")]

    def get(self, request):
        try:
            start = _date(request.query_params.get("start"), "start")
            end = _date(request.query_params.get("end"), "end")
            compare_start = _date(request.query_params.get("compare_start"), "compare_start")
            compare_end = _date(request.query_params.get("compare_end"), "compare_end")
            if end < start:
                raise ValidationError({"end": "The current period end cannot be before its start."})
            if compare_end < compare_start:
                raise ValidationError({"compare_end": "The comparison period end cannot be before its start."})
            report = comparative_income_statement(
                start_date=start, end_date=end,
                compare_start=compare_start, compare_end=compare_end,
            )
        except ValidationError as exc:
            return _error(exc)
        return Response(report)


class ComparativeBalanceSheetView(APIView):
    permission_classes = [IsAuthenticated, require("reports.report.view")]

    def get(self, request):
        try:
            as_of = _date(request.query_params.get("as_of"), "as_of")
            compare_as_of = _date(request.query_params.get("compare_as_of"), "compare_as_of")
            report = comparative_balance_sheet(
                as_of=as_of, compare_as_of=compare_as_of
            )
        except ValidationError as exc:
            return _error(exc)
        return Response(report)
