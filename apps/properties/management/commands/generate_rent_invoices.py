from datetime import date

from django.core.management.base import BaseCommand

from apps.core.tenancy import organization_context
from apps.organizations.models import Organization
from apps.properties.services import generate_due_rent_invoices
from apps.properties.tenant_space import apply_late_fees


class Command(BaseCommand):
    help = "Create (and optionally post) all rent invoices that have fallen due, for every company. Run daily."

    def add_arguments(self, parser):
        parser.add_argument("--post", action="store_true", help="Also post the invoices to the ledger.")
        parser.add_argument("--late-fees", action="store_true", help="Also charge late fees on overdue rent.")
        parser.add_argument("--date", help="Treat this date (YYYY-MM-DD) as today.")

    def handle(self, *args, **options):
        today = date.fromisoformat(options["date"]) if options["date"] else date.today()
        for organization in Organization.objects.all():
            with organization_context(organization.pk):
                result = generate_due_rent_invoices(today, post=options["post"])
                fees = apply_late_fees(today, post=options["post"]) if options["late_fees"] else None
            if fees and fees["charged"]:
                self.stdout.write(f"{organization.name}: {fees['charged']} late fee(s) charged")
            if result["generated"] or result["skipped"]:
                self.stdout.write(
                    f"{organization.name}: {result['generated']} generated, "
                    f"{result['posted']} posted, {len(result['skipped'])} skipped"
                )
                for item in result["skipped"]:
                    self.stdout.write(f"  skipped {item['tenant']} / {item['unit']}: {item['reason']}")
