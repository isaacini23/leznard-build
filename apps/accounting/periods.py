import calendar
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.access.services import has_permission
from apps.core.tenancy import get_current_org_id

from .models import AccountingPeriod, JournalEntry


def _month_bounds(day):
    last = calendar.monthrange(day.year, day.month)[1]
    return date(day.year, day.month, 1), date(day.year, day.month, last)


def _period_name(start, end):
    first, last = _month_bounds(start)
    if (start, end) == (first, last):
        return f"{start:%Y-%m}"
    return f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"


def period_for_date(day):
    """Return the active company's period covering `day`, creating a month if none exists."""
    existing = AccountingPeriod.objects.filter(start_date__lte=day, end_date__gte=day).first()
    if existing:
        return existing

    month_start, month_end = _month_bounds(day)
    previous = AccountingPeriod.objects.filter(end_date__lt=day).order_by("-end_date").first()
    following = AccountingPeriod.objects.filter(start_date__gt=day).order_by("start_date").first()
    # Squeeze the new period between neighbours so periods never overlap.
    start = month_start
    if previous and previous.end_date >= month_start:
        start = previous.end_date + timedelta(days=1)
    end = month_end
    if following and following.start_date <= month_end:
        end = following.start_date - timedelta(days=1)

    try:
        with transaction.atomic():
            return AccountingPeriod.objects.create(
                name=_period_name(start, end), start_date=start, end_date=end
            )
    except (IntegrityError, ValidationError):
        # Two requests created the same month at once: use the winner's row.
        winner = AccountingPeriod.objects.filter(start_date__lte=day, end_date__gte=day).first()
        if winner:
            return winner
        raise


def assert_period_open(day):
    """Raise unless `day` falls in an open period of the active company."""
    period = period_for_date(day)
    if period.status == AccountingPeriod.Status.CLOSED:
        raise ValidationError(
            {"entry_date": f"The accounting period {period.name} is closed. Choose a date in an open period."}
        )
    return period


def _require_period_permission(user):
    org_id = get_current_org_id()
    if org_id is None:
        raise PermissionDenied("Select a company first.")
    if not has_permission(user, "accounting.period.close", org_id):
        raise PermissionDenied("You do not have permission to close or reopen periods.")


@transaction.atomic
def close_period(period_id, *, closed_by):
    _require_period_permission(closed_by)
    try:
        period = AccountingPeriod.objects.select_for_update().get(pk=period_id)
    except AccountingPeriod.DoesNotExist:
        raise ValidationError({"period": "Period not found."})
    if period.status == AccountingPeriod.Status.CLOSED:
        raise ValidationError({"period": "This period is already closed."})
    drafts = JournalEntry.objects.filter(
        status=JournalEntry.Status.DRAFT,
        entry_date__gte=period.start_date,
        entry_date__lte=period.end_date,
    ).count()
    if drafts:
        raise ValidationError(
            {"period": f"{drafts} draft journal entr{'y' if drafts == 1 else 'ies'} in this period must be posted or removed first."}
        )
    period.status = AccountingPeriod.Status.CLOSED
    period.closed_at = timezone.now()
    period.closed_by = closed_by
    period.save()
    return period


@transaction.atomic
def reopen_period(period_id, *, reopened_by):
    _require_period_permission(reopened_by)
    try:
        period = AccountingPeriod.objects.select_for_update().get(pk=period_id)
    except AccountingPeriod.DoesNotExist:
        raise ValidationError({"period": "Period not found."})
    if period.status != AccountingPeriod.Status.CLOSED:
        raise ValidationError({"period": "This period is not closed."})
    period.status = AccountingPeriod.Status.OPEN
    period.reopened_at = timezone.now()
    period.reopened_by = reopened_by
    period.save()
    return period
