# Accounting ledger API

All endpoints are under `/api/v1/` and require an authenticated user with an active organization context.

## Chart of accounts

- `GET /accounts/` — list accounts. Optional filters: `category`, `active=true|false`.
- `POST /accounts/` — create an account. Requires `accounting.account.manage`.
- `GET /accounts/{id}/` — retrieve an account.
- `PATCH /accounts/{id}/` — update an account. Requires `accounting.account.manage`.

Create-account fields: `code`, `name`, `category`, `account_type`, `normal_balance`; optional `description`, `parent_id`, `is_active`. Account codes are unique within a company. Existing model validation enforces category/type consistency, same-company parent accounts, hierarchy-cycle prevention and protection of system-account structure.

## Journals

- `GET /journals/` — list journals. Optional filters: `status`, `start`, `end`.
- `POST /journals/` — create a draft journal. Requires `accounting.journal.manage`.
- `GET /journals/{id}/` — retrieve a journal and its lines.
- `POST /journals/{id}/post/` — post a balanced draft. Requires `accounting.journal.post`.
- `POST /journals/{id}/reverse/` — create and post an equal-and-opposite reversal. Optional body: `entry_date`, `description`.

Journal creation accepts `entry_date`, `description`, optional `reference`, `currency`, `exchange_rate`, and at least two `lines`. Each line has `account` (account UUID), `debit` or `credit`, and optional `description`. Amounts are represented as decimal strings in responses. Drafts do not affect ledger balances. Posting reuses the existing accounting service, including period-open, currency and balancing validation. Posted entries remain immutable and must be reversed rather than edited.

## General ledger and account balances

- `GET /reports/general-ledger/?account={uuid}&start=YYYY-MM-DD&end=YYYY-MM-DD`
- `GET /reports/account-balances/?as_of=YYYY-MM-DD`

The General Ledger returns the account, opening balance before `start`, dated posted/reversed journal lines, running balances and closing balance. Without `start`, the opening balance is zero and all entries through `end` are returned. Account balances return base-currency debit totals, credit totals and net debit-positive balances for each account. Draft journals are excluded; reversal journals offset their originals.

## Permissions

The new permissions are `accounting.account.view`, `accounting.account.manage`, `accounting.journal.manage`, and `accounting.ledger.view`. The migration adds the appropriate permissions to existing Manager, Accountant and Auditor roles without removing their existing permissions. Administrator access continues to use the wildcard permission.

## Financial statements

- `GET /reports/balance-sheet/?as_of=YYYY-MM-DD`
- `GET /reports/retained-earnings/?as_of=YYYY-MM-DD`
- `GET /reports/cash-flow/?start=YYYY-MM-DD&end=YYYY-MM-DD`
- `GET /reports/comparative-income-statement/?start=...&end=...&compare_start=...&compare_end=...`
- `GET /reports/comparative-balance-sheet/?as_of=...&compare_as_of=...`
- `GET /reports/comparative-cash-flow/?start=...&end=...&compare_start=...&compare_end=...`

All reports use posted and reversed journals in the company's base currency and exclude drafts. Date inputs must be exact `YYYY-MM-DD` values; date ranges reject an end before the start. Comparative reports return absolute variance and percentage variance. Percentage variance is `null` when the comparison value is zero.

The Cash Flow Statement uses the indirect method: net profit, non-cash adjustments, and changes in operating working capital form operating cash flow. Investing and financing movements use explicit journal-line classification when provided, with account-type fallbacks for known asset and financing accounts. Unknown movements are reported separately as unclassified; inspect them and the reconciliation difference before relying on the statement. Cash-to-bank and bank-to-bank transfers net to zero.

A non-cash counterpart journal line may include `cash_flow_category` with one of `operating`, `investing`, `financing`, or `non_cash_adjustment`. Use this for ambiguous or mixed-purpose transactions. The classification is retained when the journal is reversed.

Year-end closing journals are explicitly marked with `is_year_end_closing: true` and must include `closing_period_end: YYYY-MM-DD`, the fiscal period end being closed. The closing journal's entry date must be on or after that period end. This lets retained earnings distinguish closed prior/current profits from unclosed earnings and prevents double counting. Reversals preserve the closing metadata.

This reporting stage adds accounting migrations for journal-line cash-flow classification and year-end closing metadata. Apply migrations before using the new fields.

## Scope note

This stage extends financial reporting. Dedicated cash/bank account management, transfers UI, bank statement imports and automated bank reconciliation remain a separate subsequent batch.
