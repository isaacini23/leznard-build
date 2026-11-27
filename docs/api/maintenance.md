# Maintenance requests

Repair tickets for a property or one unit. Requests are never deleted: they are completed or cancelled.

- `GET /api/v1/maintenance/` (`properties.maintenance.view`): filters `property`, `unit`, `status`, `priority`, `category`, `assigned_to`, `active=1` (open and in progress).
- `POST /api/v1/maintenance/` (`properties.maintenance.manage`): `property`, `title` required; optional `unit` (must belong to the property), `reported_by` (any contact), `assigned_to` (must be a contractor, subcontractor or supplier), `category`, `priority`, `description`, `reported_date` (default today), `scheduled_date`, `estimated_cost`.
- `GET/PATCH /api/v1/maintenance/{id}/`: edit while open or in progress.
- `POST /api/v1/maintenance/{id}/start/` `{scheduled_date?}`; `POST .../complete/` `{completed_date?, actual_cost?, resolution_notes?}`; `POST .../cancel/` `{reason?}`.

Costs are recorded on the request only; posting repair costs to the ledger (through a purchase bill) is a later step.
Built-in roles: Manager and Property officer can manage; Employee, Accountant and Auditor can view.

## Billing a completed job

`POST /api/v1/maintenance/{id}/bill/` `{bill_date?, post?}` turns the actual cost of a completed job into a purchase bill from the assigned contractor, charged to Repairs and maintenance and tagged to the property (so it appears in property profitability). The contractor needs the Supplier role so the bill can be paid; a job can be billed once. `post: true` also posts it to the ledger and needs `purchases.bill.post`; otherwise it stays a draft.
