"""Read-only property profitability reporting API."""
from datetime import date

from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.properties.models import Property
from apps.properties.reports import owner_statement, property_profitability


def _plain(value):
    if isinstance(value, list):
        return [{k: _plain(v) for k, v in row.items()} for row in value]
    if hasattr(value, "as_tuple"):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class PropertyProfitabilityView(APIView):
    permission_classes = [IsAuthenticated, require("properties.profitability.view")]
    builder = staticmethod(property_profitability)

    def get(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return Response(status=404)

        try:
            start = date.fromisoformat(request.query_params.get("start", ""))
        except (TypeError, ValueError):
            raise ValidationError({"start": "Use a date like 2026-01-01."})
        try:
            end = date.fromisoformat(request.query_params.get("end", ""))
        except (TypeError, ValueError):
            raise ValidationError({"end": "Use a date like 2026-01-31."})
        if end < start:
            raise ValidationError({"end": "The end date cannot be before the start date."})

        report = self.builder(prop, start_date=start, end_date=end)
        return Response({key: _plain(value) for key, value in report.items()})


class OwnerStatementView(PropertyProfitabilityView):
    """GET /properties/<id>/owner-statement/?start=&end=: what each owner is owed."""

    builder = staticmethod(owner_statement)
