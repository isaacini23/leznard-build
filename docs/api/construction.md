# Construction projects

Everything is a normal sales invoice, purchase bill or payment underneath, so the ledger, periods and audit rules apply as usual. Projects only link those documents together so a project can be costed.

Permissions: `construction.project.view` to read, `construction.project.manage` to edit projects, budgets, variations, subcontracts and to charge bills to a project, `construction.payment.record` for money actions (progress invoices, certifying, paying claims, releasing retention). Manager has all three; Accountant has view and record; Auditor has view.

## Projects and budgets
- `GET/POST /api/v1/projects/` `{code, name, client (needs Customer role), contract_value, status?, start_date?, end_date?, site_address?}`; `GET/PATCH /projects/{id}/`. Codes are unique per company. Nothing is deleted.
- `GET/POST /projects/{id}/budget/` `{cost_type (labour, materials, subcontractors, equipment, overhead, other), description, budget_amount}`; `PATCH /budget-lines/{id}/`.
- `GET/POST /projects/{id}/variations/` `{description, amount}` (negative for an omission); `POST /variations/{id}/approve|reject/`. Approved variations change the contract value.

## Costs
`GET/POST /projects/{id}/costs/` `{purchase_bill, budget_line?}` charges an existing purchase bill to the project. A bill can belong to one project. Only posted bills count as cost to date (excluding input tax).

## Billing the client
`GET/POST /projects/{id}/invoices/` `{amount, description?, invoice_date?, due_days?, revenue_account?, post? (default true)}` creates a progress invoice to the client. The total billed can never exceed the contract value including approved variations. Receipts use the normal payments API.

## Subcontractors, claims and retention
- `GET/POST /projects/{id}/subcontracts/` `{subcontractor (needs Subcontractor role), description, contract_sum, retention_percent (default 5), budget_line?}`; `GET/PATCH /subcontracts/{id}/`.
- `GET/POST /subcontracts/{id}/claims/` `{gross_amount, claim_date?, description?}`: retention is calculated automatically; claims cannot exceed the contract sum.
- `POST /claims/{id}/certify/` `{expense_account?, due_days?, post? (default true)}` creates the purchase bill for the gross value, charged to the project and its budget line (the subcontractor needs the Supplier role so they can be paid).
- `POST /claims/{id}/pay/` `{account, amount?}` pays the net (gross minus retention). `POST /claims/{id}/release-retention/` `{account, amount?}` pays held retention, only after the net is paid. Retention is simply the part of the bill left unpaid, so accounts payable always shows exactly what is held. `POST /claims/{id}/cancel/` cancels a draft claim.

## Report
`GET /projects/{id}/summary/`: contract (original, approved variations, current value, billed, left to bill, received, client owes), costs (budget, cost to date, remaining, subcontracts committed, retention held), profit (to date and expected margin) and every budget line with actual, remaining, percent used and an over-budget flag.

Not included yet: client retention on progress invoices, bills of quantities, site diaries, daily labour records and equipment logs.

## Manager posting
Managers can now post sales invoices, purchase bills and payments (`sales.invoice.post`, `purchases.bill.post`, `payments.payment.post`), so a business with no accountant is never blocked by drafts. Accountants keep these and remain optional.
