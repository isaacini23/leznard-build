"""Read-only property profitability reporting API."""
from datetime import date

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.drf import require
from apps.properties.models import Property
from apps.properties.reports import property_profitability


class PropertyProfitabilityView(APIView):
    permission_classes = [IsAuthenticated, require("properties.profitability.view")]

    def get(self, request, property_id):
        prop = Property.objects.filter(pk=property_id).first()
        if prop is None:
            return Response(status=404)

        try:
            start = date.fromisoformat(request.query_params.get("start", ""))
        except (TypeError, ValueError):
            raise DjangoValidationError({"start": "Use a date like 2026-01-01."})
        try:
            end = date.fromisoformat(request.query_params.get("end", ""))
        except (TypeError, ValueError):
            raise DjangoValidationError({"end": "Use a date like 2026-01-31."})
        if end < start:
            raise DjangoValidationError({"end": "The end date cannot be before the start date."})

        report = property_profitability(prop, start_date=start, end_date=end)
        return Response({
            key: (str(value) if hasattr(value, "as_tuple") else value.isoformat() if hasattr(value, "isoformat") else value)
            for key, value in report.items()
        })
