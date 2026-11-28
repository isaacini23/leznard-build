# Owner reports

Plain-language reports for business owners. Amounts are in the company's base currency, as strings with two decimals.

- `GET /api/v1/reports/owner-summary/?month=2026-11` (default: current month; needs `reports.report.view`)
  - `units`: total, occupied, vacant, under_maintenance, occupancy_percent
  - `rent`: invoiced_this_month, collected_this_month, owed_now, overdue_now, tenants_with_arrears
  - `money`: money_in and money_out (posted receipts/payments in the month), net, customers_owe_you, you_owe_suppliers
- `GET /api/v1/reports/rent-owed/` (needs `properties.lease.view`; add `?overdue=1` for late rent only): each unpaid rent invoice with tenant, property, unit, due date, amount due and days overdue, oldest first.
