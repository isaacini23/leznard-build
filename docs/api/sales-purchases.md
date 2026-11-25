# Sales invoices and purchase bills API

All endpoints require an authenticated session and an active organization selected with `X-Organization-ID`.

## Sales invoices

- `GET /api/v1/sales-invoices/` — list invoices; optional `?status=draft|posted`.
- `POST /api/v1/sales-invoices/` — create a draft invoice with nested lines.
- `GET /api/v1/sales-invoices/{id}/` — retrieve an invoice.
- `PATCH /api/v1/sales-invoices/{id}/` — update a draft invoice.
- `POST /api/v1/sales-invoices/{id}/post/` — post the invoice to the general ledger.

Each line contains `description`, `quantity`, `unit_price`, optional `discount_amount` and `tax_amount`, `revenue_account`, and (when tax is charged) `tax_account`.

## Purchase bills

- `GET /api/v1/purchase-bills/` — list bills.
- `POST /api/v1/purchase-bills/` — create a draft bill.
- `GET /api/v1/purchase-bills/{id}/` — retrieve a bill.
- `PATCH /api/v1/purchase-bills/{id}/` — update a draft bill.
- `POST /api/v1/purchase-bills/{id}/post/` — post the bill to the general ledger.

Each bill line contains `description`, `quantity`, `unit_price`, optional `discount_amount` and `tax_amount`, `expense_account`, and (when input tax is recorded) `tax_account`.

Amounts are decimal strings in API responses. Tax rates are not assumed or calculated by Leznard in this phase: the entered tax amounts and selected tax accounts are recorded explicitly. Posting respects the organization's base currency, exchange rate, and open accounting period. Posted documents are immutable. Payments, inventory movements, PDF generation, email delivery, and void/reversal workflows are outside this phase.
