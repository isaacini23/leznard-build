# Rent Management API

All endpoints require an authenticated session and an active organization selected with `X-Organization-ID`.

Rent billing is configured for an active tenancy. The tenancy's rent amount and frequency are the source of the charge amount and billing interval. Select an active income account for the rent revenue.

## Configure a rent schedule

`POST /api/v1/tenancies/{tenancy_id}/rent-schedule/`

```json
{
  "revenue_account": "INCOME_ACCOUNT_UUID",
  "next_invoice_date": "2027-01-31",
  "due_days": 5
}
```

The billing day is taken from `next_invoice_date` and retained when later months have fewer days (for example, a schedule anchored on day 31 advances to the last day of February and returns to day 31 in March). `due_days` may be from 0 to 90.

- `GET /api/v1/tenancies/{tenancy_id}/rent-schedule/` — retrieve the schedule.
- `POST /api/v1/tenancies/{tenancy_id}/rent-schedule/` — create the one schedule for a tenancy.
- `PATCH /api/v1/tenancies/{tenancy_id}/rent-schedule/` — change the next invoice date, revenue account, payment terms, or active state.

## Generate the next rent invoice

`POST /api/v1/rent-schedules/{schedule_id}/generate/`

Generates one draft sales invoice for the next complete rent period, records the period against the tenancy, and advances the schedule. The operation is atomic and prevents the same tenancy period from being invoiced twice. A period that would extend beyond the tenancy end date is refused; prorated final rent is not yet supported.

The invoice is a normal sales invoice linked to the tenancy. It must be posted through:

`POST /api/v1/sales-invoices/{invoice_id}/post/`

Posting debits accounts receivable and credits the selected rent income account. It respects the accounting period and existing sales invoice posting controls.

## Rent history and balances

- `GET /api/v1/tenancies/{tenancy_id}/rent-charges/` — list rent periods, linked invoice IDs, invoice state, due dates, paid amounts, outstanding amounts, and rent status.

Rent statuses are `draft`, `unpaid`, `partially_paid`, `overdue`, and `paid`. An overdue charge remains marked overdue while a balance is outstanding, including where it has been partially paid.

Record rent receipts through the existing customer receipt endpoint. The contact must have the Tenant or Customer role, the receipt must use the tenancy tenant as its contact, and the allocation points to the generated sales invoice. Existing payment posting controls and immutable allocations apply.

Permissions: `properties.lease.view` to view schedules and rent charges; `properties.lease.manage` to configure schedules and generate draft invoices; `sales.invoice.post` to post rent invoices. Property officers with `properties.payment.record` may record only tenant receipts allocated to posted rent invoices; accountants or users with `payments.payment.post` post receipts. Users with `payments.payment.manage` retain the existing general payment workflow.

Deposits/refunds, prorated final periods, automated recurring jobs/reminders, lease renewals, and property maintenance are not included in this phase.

## One-step actions for owners

No accounting knowledge is needed to bill and collect rent.

- `POST /api/v1/rent-schedules/{id}/generate/` with `{"post": true}` generates the invoice and posts it to the ledger.
- `POST /api/v1/rent-charges/{charge_id}/post/` posts a draft rent invoice (needs `properties.lease.manage`).
- `POST /api/v1/rent-charges/{charge_id}/pay/` (needs `properties.payment.record`) records a tenant's rent payment end to end: it posts the invoice if still a draft, creates the receipt, allocates it and posts it. All fields are optional: `amount` (default: everything still owed; part payments are allowed, overpaying is refused), `payment_date` (default today), `account` (default: the first active bank, else cash account), `method`, `reference`. Returns the updated rent charge.
