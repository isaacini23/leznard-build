"""Plain-language project reports: budget against actual, billing, profit and retention."""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum

from apps.payments.models import Payment, PaymentAllocation
from apps.sales.models import PurchaseBill, SalesInvoice

from .models import BudgetLine, Project, ProjectCost, ProjectInvoice, Subcontract, SubcontractClaim, Variation
from .services import effective_contract_value, invoiced_to_date

ZERO = Decimal("0")
CENT = Decimal("0.01")


def _m(value):
    return format(Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP), "f")


def _bill_cost(cost):
    bill = cost.purchase_bill
    if bill.status != PurchaseBill.Status.POSTED:
        return ZERO
    return bill.subtotal * bill.exchange_rate


def project_summary(project: Project):
    costs = list(ProjectCost.objects.filter(project=project).select_related("purchase_bill", "budget_line"))
    actual_by_line, unallocated = {}, ZERO
    for cost in costs:
        value = _bill_cost(cost)
        if cost.budget_line_id:
            actual_by_line[cost.budget_line_id] = actual_by_line.get(cost.budget_line_id, ZERO) + value
        else:
            unallocated += value
    cost_to_date = sum(actual_by_line.values(), ZERO) + unallocated

    lines, budget_total = [], ZERO
    for line in BudgetLine.objects.filter(project=project):
        actual = actual_by_line.get(line.pk, ZERO)
        budget_total += line.budget_amount
        lines.append({
            "id": str(line.pk), "cost_type": line.cost_type, "description": line.description,
            "budget": _m(line.budget_amount), "actual": _m(actual),
            "remaining": _m(line.budget_amount - actual),
            "percent_used": _m(actual * 100 / line.budget_amount) if line.budget_amount else "0.00",
            "over_budget": actual > line.budget_amount,
        })

    effective = effective_contract_value(project)
    invoiced = invoiced_to_date(project)
    links = list(ProjectInvoice.objects.filter(project=project).select_related("invoice"))
    billed_total = sum((l.invoice.total * l.invoice.exchange_rate for l in links if l.invoice.status == "posted"), ZERO)
    received = ZERO
    for alloc in PaymentAllocation.objects.filter(
        sales_invoice__project_invoice__project=project, payment__status=Payment.Status.POSTED
    ).select_related("payment"):
        received += alloc.amount * alloc.payment.exchange_rate

    claims = SubcontractClaim.objects.filter(
        subcontract__project=project, status=SubcontractClaim.Status.CERTIFIED
    )
    retention_held = sum((c.retention_held for c in claims), ZERO)
    committed = Subcontract.objects.filter(project=project, status=Subcontract.Status.ACTIVE).aggregate(
        t=Sum("contract_sum")
    )["t"] or ZERO

    return {
        "project_id": str(project.pk), "code": project.code, "name": project.name, "status": project.status,
        "contract": {
            "original_value": _m(project.contract_value),
            "approved_variations": _m(effective - project.contract_value),
            "current_value": _m(effective),
            "billed_to_date": _m(invoiced),
            "left_to_bill": _m(effective - invoiced),
            "received_from_client": _m(received),
            "client_owes": _m(max(billed_total - received, ZERO)),
        },
        "costs": {
            "budget_total": _m(budget_total),
            "cost_to_date": _m(cost_to_date),
            "unallocated_cost": _m(unallocated),
            "budget_remaining": _m(budget_total - cost_to_date),
            "subcontracts_committed": _m(committed),
            "retention_held_from_subcontractors": _m(retention_held),
        },
        "profit": {
            "profit_to_date": _m(invoiced - cost_to_date),
            "expected_margin": _m(effective - max(budget_total, cost_to_date)),
            "expected_margin_percent": _m((effective - max(budget_total, cost_to_date)) * 100 / effective) if effective else "0.00",
        },
        "budget_lines": lines,
        "lines_over_budget": sum(1 for l in lines if l["over_budget"]),
    }
