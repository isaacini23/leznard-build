"""Owner-facing summary reports."""
from datetime import date

from django.utils import timezone

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.properties.owner_reports import owner_summary, rent_roll


class OwnerSummaryView(APIView):
    """GET /api/v1/reports/owner-summary/?month=2026-11 (default: this month)."""

    permission_classes = [IsAuthenticated, require("reports.report.view")]

    def get(self, request):
        today = timezone.localdate()
        raw = request.query_params.get("month")
        try:
            year, month = (int(p) for p in raw.split("-")) if raw else (today.year, today.month)
            date(year, month, 1)
        except (ValueError, TypeError):
            raise ValidationError({"month": "Use a month like 2026-11."})
        return Response(owner_summary(year, month, today))


class RentRollView(APIView):
    """GET /api/v1/reports/rent-owed/: who owes rent, oldest first."""

    permission_classes = [IsAuthenticated, require("properties.lease.view")]

    def get(self, request):
        rows = rent_roll(timezone.localdate())
        if request.query_params.get("overdue") == "1":
            rows = [r for r in rows if r["days_overdue"]]
        return Response(rows)
