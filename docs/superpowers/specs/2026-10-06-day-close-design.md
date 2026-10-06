# Day close (cash count) with closed-day lock — design

Date: 2026-10-06. Approved by owner in chat.

## Goal
Let staff close a day by entering the counted cash and Whish balance. The difference becomes an
over/short adjustment so the next day starts from the counted amounts, and every day up to the last
closed day is locked against staff edits.

## Owner decisions
- Admin or finance can close a day; only admin can reopen, and only the latest close.
- Collector cash confirmed after its (closed) collection day is ALLOWED and flagged "changed after close".
- Automatic paths (Whish success callbacks, credit settlement, scheduler) are never blocked.

## Business timezone (prerequisite)
- `BusinessSettings.timezone` String(64), default `'Asia/Beirut'` (IANA, via `zoneinfo`; add `tzdata` to
  requirements so Windows on-prem installs work). Expose it in the business-settings GET/PUT and validate it.
- Helper `_local_day(tenant_settings, dt, calendar)`: for calendar-type columns a midnight value is the typed
  date; otherwise convert the UTC instant to the tenant zone. This replaces the browser-derived offset in
  `_cash_flow_entries` / `_cash_flow_for_day` / `get_daily_cash_report`, so every viewer sees the same days
  and the lock agrees with the register. DST is handled per row.
- `/api/reports/daily-cash` may also accept `?day=YYYY-MM-DD`; the old start/end params keep working
  (day = local date of start + 12h, as today).

## Model `DayClose`
id, tenant_id, day (Date, unique per tenant), expected_cash, counted_cash, expected_whish, counted_whish
(Numeric 18,4), note String(200) nullable, closed_by_id, closed_at. Differences are derived
(counted − expected). One Alembic migration (random revision id, single head) adds the table and the
timezone column.

## Rules
- Close day D only if: an opening balance exists and D >= opening date; D <= today (tenant zone);
  D > the latest closed day (if any). Expected values = the register's cash_end / whish_end for D
  computed at close time.
- `locked_through` = the latest DayClose.day. A day is locked iff day <= locked_through.
- Reopen: admin only, only the latest DayClose; deletes it (its adjustment disappears).
- Opening balance (`PUT /api/reports/cash-opening`) is refused while any DayClose exists.

## Cash flow integration
- Each DayClose contributes, on its day, per account: diff > 0 → in "Over/short adjustment";
  diff < 0 → out "Over/short adjustment" (abs value). Description = note or "Cash count".
  These flow through running balances, so next-day start == counted + any late movements.
  The adjustment is included in combined totals (it is real money, not a transfer).
- `_cash_flow_for_day` response adds `close`: null or {day, expected_cash, counted_cash, cash_diff,
  expected_whish, counted_whish, whish_diff, note, closed_by, closed_at,
  late: {cash, whish, count}} where late = movements on that day whose record was created/confirmed after
  closed_at (see below), and `locked_through` (date or null).
- "Changed after close" detection: compare the day's current cash_end/whish_end (excluding the adjustment)
  with the stored expected values; late = current − expected per account. Non-zero → flagged.

## Lock enforcement (staff paths only)
Guard helper `_assert_day_open(day)` → 409 `{error: "Day YYYY-MM-DD is closed — ask an admin to reopen it.",
code: "day_closed"}`. Check both the record's current day (before change) and its new day (after change).
Apply to:
- Expense: POST, PUT, DELETE `/api/expenses…`; POST `/api/employees/<id>/payments`.
- SupplierPayment: POST, PUT, DELETE.
- ResellerPayment: POST `/api/resellers/<id>/collect_payment` (dated now → only blocked if today is closed).
- UpstreamProviderPayment: POST `/api/upstream-providers/<id>/topup` (same).
- CashEntry / AccountTransfer: POST, DELETE.
- Payment staff paths: mark_paid `pay` (but NOT when the payment was already collected on a closed day —
  that is the allowed late confirm), bulk_mark_paid (same exception), receive-payment, refund, revert,
  set_payment_method, mark_gratis on a paid row, DELETE /payments/<id>, bulk_delete payments, and
  delete_customer / bulk_delete_customers when any affected paid payment falls on a locked day.
  confirm-collected is allowed (late confirm).
- NOT guarded: customer_whish_success, customer_whish_attempt_success, apply_customer_balance_to_unpaid_payments,
  schedulers, tenant delete, unpaid-row creation (not in cash flow).
Dates that default to "now" use the tenant-zone today.

## Routes
- `GET /api/day-closes?limit=` (admin/finance): history newest first with diffs.
- `POST /api/day-closes` {day, counted_cash, counted_whish, note} (admin/finance) → 201; 400/409 on rule violations.
- `DELETE /api/day-closes/<id>` (admin; latest only, else 409).

## Frontend (CashFlowRegister / DailyCashFlowSection)
- "Close day" button (admin/finance) when the selected day is closable → dialog showing expected cash/Whish,
  counted inputs (prefilled with expected), live difference, note.
- Closed-day banner: "Closed by X at HH:MM", expected vs counted vs over/short per account; warning chip
  "Changed after close: +N" when late ≠ 0; admin "Reopen" on the latest close (confirm first).
- On locked days disable "Cash in"/"Transfer" and hide delete buttons; show the 409 message on any blocked
  action elsewhere (existing error snackbars should display `error`).
- "Closes history" collapsible table under the register.
- Opening-balance edit button hidden/disabled while closes exist, with a tooltip.

## Testing
pytest: close rules (order, future, before opening, duplicate), reopen latest-only + admin-only, adjustment
entries and next-day start == counted, lock 409 on each guarded path, late confirm allowed + flagged,
Whish callback on a closed day allowed, opening edit refused, timezone boundaries (23:30 Beirut lands on the
right day, DST). Jest: close dialog difference, banner + late chip, buttons disabled on locked days.
Live check on a throwaway DB.
