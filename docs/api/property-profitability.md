# Property profitability

A property owner or authorised property manager can read the profitability report for an individual property. Accountant access is optional; it is not required to operate the property or view reports.

## Endpoint

`GET /api/v1/properties/{property_id}/profitability/?start=YYYY-MM-DD&end=YYYY-MM-DD`

Requires authentication, an active organisation context, and `properties.profitability.view`.

The built-in Administrator, Manager and Property officer roles receive this permission. Accountant and Auditor roles also receive read access when they are members of the organisation or have an appropriate client access grant. The Employee role does not receive it by default.

Both dates are required. The end date must not be earlier than the start date. The property is resolved through the active organisation, so another organisation's property returns 404.

## Calculation rules

All monetary values are returned as decimal strings in the organisation's base currency.

- **Rent billed:** total of posted rent invoices whose document dates fall inside the selected period. Draft rent invoices are excluded.
- **Rent collected:** allocations to posted rent invoices, where the receipt itself is posted and its payment date falls inside the selected period.
- **Outstanding rent:** unpaid balance of posted rent invoices dated on or before the report end date, including invoices from before the selected period.
- **Operating expenses:** net line amounts (excluding input VAT, which is recorded as an asset) on posted purchase bills linked to this property and dated inside the selected period.
- **Operating expenses paid:** posted supplier-payment allocations against those property purchase bills, based on payment date inside the selected period.
- **Net operating income:** rent billed less operating expenses (accrual view).
- **Cash net operating income:** rent collected less operating expenses paid (cash view).

Only purchase bills explicitly linked to a property are attributed to that property. General journal expenses and purchase bills without a property link are not allocated across properties. Existing bills may be linked to a property while still in draft; posted bills remain immutable.

## Example response

```json
{
  "property_id": "00000000-0000-0000-0000-000000000000",
  "property_name": "Palm Court",
  "currency": "NGN",
  "start_date": "2027-01-01",
  "end_date": "2027-01-31",
  "rent_billed": "120000.00",
  "rent_collected": "50000.00",
  "outstanding_rent_as_of_end_date": "70000.00",
  "operating_expenses": "15000.00",
  "operating_expenses_paid": "5000.00",
  "net_operating_income": "105000.00",
  "cash_net_operating_income": "45000.00",
  "rent_invoice_count": 1,
  "expense_bill_count": 1
}
```
