# Payments API

All endpoints require an authenticated session and an active organization selected with `X-Organization-ID`.

## Record a customer receipt

`POST /api/v1/payments/`

```json
{
  "payment_type": "customer_receipt",
  "payment_date": "2026-02-15",
  "contact": "CUSTOMER_CONTACT_UUID",
  "account": "CASH_OR_BANK_ACCOUNT_UUID",
  "amount": "50000.0000",
  "currency": "NGN",
  "exchange_rate": "1",
  "method": "bank_transfer",
  "reference": "TRF-001",
  "allocations": [
    {"sales_invoice": "INVOICE_UUID", "amount": "30000.0000"}
  ]
}
```

## Record a supplier payment

Use `payment_type: "supplier_payment"` and allocate against `purchase_bill` instead of `sales_invoice`.

A payment may be allocated across multiple posted documents. Allocation totals cannot exceed the payment amount, and posting refuses allocations that exceed a document's remaining balance. The unallocated amount remains an on-account customer/supplier balance for later allocation; this phase does not yet provide a separate credit-note/refund workflow.

## Endpoints

- `GET /api/v1/payments/` — list payments; optional `?type=customer_receipt|supplier_payment` and `?status=draft|posted`.
- `POST /api/v1/payments/` — create a draft payment with optional allocations.
- `GET /api/v1/payments/{id}/` — retrieve a payment and its allocations.
- `POST /api/v1/payments/{id}/post/` — post to the general ledger.

Posting a customer receipt debits the selected cash/bank account and credits accounts receivable. Posting a supplier payment debits accounts payable and credits the selected cash/bank account. Posted payments and their allocations are immutable. Posting respects the payment currency, exchange rate, and open accounting period.

Permissions: `payments.payment.view`, `payments.payment.manage`, and `payments.payment.post`. Managers can record drafts; accountants can post; auditors can view.

Invoice and bill payment progress is exposed by the existing sales/purchase document APIs as `amount_paid`, `amount_due`, and `payment_status`.
