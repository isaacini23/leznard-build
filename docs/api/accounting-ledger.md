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

## Scope note

This stage establishes the ledger and API layer. Balance Sheet, Cash Flow Statement, comparative schedules, bank accounts, cashbook, transfers, statement imports and bank reconciliation are subsequent stages in the approved accounting programme.
