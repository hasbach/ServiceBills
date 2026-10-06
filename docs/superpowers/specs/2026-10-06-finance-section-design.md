# Finance section, manual cash-in, account transfers — design

Date: 2026-10-06. Approved by owner in chat.

## Goal
Group all financial-record views under one **Finance** nav item, and let staff record
manual cash-in entries and Cash⇄Whish transfers that flow into the daily cash register.

## Decisions (owner)
- One `Finance` nav item replaces Resellers, Suppliers, Payroll (employees), Expenses nav items.
  "Daily Cash" is removed from the Enhanced Reports dropdown and becomes Finance → Cash Flow.
- Manual entries are **cash-in only** (outflows stay in Expenses).
- Entries/transfers: admin or finance can create; **admin only** can delete; no edit.

## Backend (app.py)
Models (tenant-scoped like the rest):
- `CashEntry`: id, tenant_id, account (`'cash'|'whish'`, String(10), not null), amount Numeric(18,4) > 0,
  reason String(200) required, date DateTime (typed date at local midnight, same convention as Expense.date),
  created_by_id (FK user, nullable), created_at.
- `AccountTransfer`: id, tenant_id, from_account, to_account (both `'cash'|'whish'`, must differ),
  amount Numeric(18,4) > 0, note String(200) optional, date DateTime, created_by_id, created_at.
- One Alembic migration, random revision id, must leave a single head.

Routes:
- `GET /api/cash-entries?start_date&end_date`, `POST /api/cash-entries` — admin_or_finance.
- `DELETE /api/cash-entries/<id>` — admin_required.
- Same three for `/api/account-transfers`.
- 400 on invalid account, amount <= 0, missing reason, from == to.

Cash flow (`_cash_flow_entries` / `_cash_flow_for_day`):
- CashEntry → direction in, category `Manual cash-in`, channel = account, description = reason.
- AccountTransfer → out on from_account with category `Transfer to Whish|Cash`, and in on
  to_account with category `Transfer from Cash|Whish`; description = note.
- Running balances (cash_start/end, whish_start/end) include transfers and entries.
- Combined totals (`total_in/out/net` and combined items) **exclude transfer categories**;
  `total_start/end` are unaffected because transfers net to zero.
- Add the new categories to the in/out category ordering constants.

## Frontend
- `FinanceView.js`: MUI Tabs Cash Flow | Payroll | Suppliers | Resellers | Expenses.
  Roles: admin sees all; finance sees Cash Flow, Suppliers, Resellers. Existing views are embedded unchanged.
- `CashFlowRegister.js`: the day picker + `DailyCashFlowSection` + collector table moved out of
  `EnhancedReportsView.renderDailyCashReport`; adds "Cash in" and "Transfer" dialogs and a list of the
  day's manual entries/transfers with delete (admin only). Refetches after any change.
- `App.js`: NAV_ITEMS gets `finance` (admin, finance); old keys `resellers|suppliers|employees|expenses`
  map to `finance` with the matching tab (deep links via `?view=` keep working).
- `EnhancedReportsView.js`: drop the `daily-cash` option and its render path.

## Testing
- pytest: CRUD + role checks (finance cannot delete), validation errors, cash flow includes entries and
  both transfer legs, combined totals exclude transfers, running balances after a transfer.
- Jest: FinanceView tab filtering by role; existing DailyCashFlowSection tests still pass.
- Live check on a local server pinned to a throwaway DB (never the dev DB).
