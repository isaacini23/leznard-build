# Tenant space: late fees, reminders, owner payouts, tenant portal

## Late fees
Set on the rent schedule (`PATCH /tenancies/{id}/rent-schedule/`): `late_fee_percent` (of what is still owed), `late_fee_flat`, `late_fee_grace_days`. Then:
- `POST /api/v1/rent-schedules/apply-late-fees/` `{post?}` (`properties.lease.manage`) charges every overdue rent invoice its fee as a separate invoice to the tenant, once only, after the grace period. Returns `{charged, fees[], skipped}`.
- `manage.py generate_rent_invoices --late-fees` does it for every company (add `--post` to post the invoices). Run daily with the invoicing job.

## Reminders
`GET /api/v1/reports/rent-reminders/?days_before=3` (`properties.lease.view`): each overdue or soon-due rent with the tenant's phone and email and a ready-to-send message (`kind` is `overdue` or `upcoming`). Nothing is sent automatically; the owner copies the text into WhatsApp, SMS or email.

## Owner payouts
`GET/POST /api/v1/properties/{id}/owner-payouts/`. POST (`properties.payment.record`): `{owner, period_start, period_end, account, amount?, payout_date?, reference?}` pays an owner what the period's owner statement says they are owed (default: everything still owed; never more) and posts it to the ledger (debit Owner Payouts 3300, credit the bank or cash account). The owner statement now shows `paid` and `outstanding` per owner. Payouts are never edited or deleted.

## Tenant portal
Tenants get their own limited view; they are not company members and cannot reach any company endpoint.
- Landlord: `POST /api/v1/contacts/{id}/portal-invite/` (`properties.lease.manage`) returns a one-time `token` (14 days). Give it to the tenant.
- Tenant (signed in with their own account): `POST /api/v1/portal/accept/` `{token}` links their account to that tenant. One person per tenant; the code works once.
- `GET /api/v1/portal/` tenancies; `GET /portal/rent/` their rent invoices with paid, balance and status (`paid`, `due`, `part_paid`, `overdue`; drafts hidden); `GET /portal/deposit/`; `GET/POST /portal/maintenance/` to see and report repairs (only for a home they currently rent). If a user is a tenant of several companies, add `?company=<id>`.
