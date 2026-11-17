# Books (double-entry ledger) — design

Date: 2026-11-17. Roadmap step **1C** of `docs/superpowers/plans/2026-11-16-finance-accounting-roadmap.md`.
Status: design, awaiting owner approval. Nothing built.

## Why

The owner wants the finance section to work like professional accounting software: figures that can be trusted and controlled, and a base that the 1D reports (profit and loss, aging, statements) and the Phase 2 inventory module build on. Today each party balance is a single number that every route edits by hand. 1A fixed many places where those edits went wrong, and 1B added History so changes can be traced. The books make "every money event is a balanced, permanent entry" true, so any balance can be explained line by line.

## Goals

- Every money event writes a balanced journal entry (total debits = total credits) in the same database transaction as the event itself.
- Entries are never edited or deleted. A change or delete of a record writes a new correcting entry.
- Staff keep using the same screens. Nothing in the daily workflow changes.
- Every night, each stored balance (customer, reseller, supplier, employee, upstream provider) is checked against the books. Mismatches are listed, never silently fixed.
- A read-only **Books** view for admins: chart of accounts with balances (trial balance), the journal with filters, and the nightly check result.

## Non-goals (this step)

- Switching the existing reports (Dashboard, Finance, Reports, Cash Flow) to the books. They stay exactly as they are during the **shadow period**, and switch in a later step only once both agree (owner decision).
- Profit and loss, aging, statements, month close, exports, manual journal entries, owner drawings / capital classification, supplier bills — all roadmap 1D.
- Rebuilding history before the start date, or correcting old balances (owner decisions: no history rebuild, no data repair).
- Currency: USD only (owner rule). `currency` / `fx_rate_to_reporting` are ignored.
- A bank account: money accounts are Cash and Whish only (owner decision).
- Collected-but-not-confirmed money (held by a collector): no entry until the bill is marked paid, exactly as the cash reports treat it today.

## Anti-goals (would make this a failure even if it "works")

- A money event that commits without its journal entry, or a journal entry without its event.
- The books slowing down or breaking a payment, renewal or the nightly billing run. A bug in the books must be visible (flagged), but must not lose the user's payment.
- Any change to what staff see or do today.
- Old wrong balances "fixed" by the books (no data repair).

## Decisions already made

| Decision | By | When |
|---|---|---|
| Build a double-entry journal | owner | 2026-11-16 |
| Start from a date with opening balances = stored balances as they are; no history rebuild | owner | 2026-11-16 |
| Cash basis stays the default profit view; earned/owed view later | owner | 2026-11-16 |
| Money accounts: Cash and Whish only | owner | 2026-11-16 |
| Books start **on the day they go live** (first month is a half month; from the next month every month is 1st → last day) | owner | 2026-11-17 |
| Entries written at the save point (option 1 below) | owner | 2026-11-17 |
| Deleting a customer keeps their received money and income in the books; only their leftover balance is written off | owner (took Claude's pick) | 2026-11-17 |

## Approaches considered

1. **Hooked in at the save point (chosen by the owner).** A SQLAlchemy flush listener, next to the History (audit log) listener, turns every create / change / delete of a financial record into journal lines in the same transaction. Covers every path automatically — screens, nightly billing, Whish callbacks, the AI connector — including routes added later. Cost: one central mapping that needs thorough tests.
2. **Written by each route.** About 25 places to keep in step; a forgotten route is a silent gap. Rejected: that class of bug is what 1A spent days fixing.
3. **Built nightly from the records.** Always a day behind, and edits/deletes lose their old values. Rejected.

## How it works

### Accounts (chart of accounts)

Created automatically per business when its books start. Each has a fixed internal key (code depends on the key, never on the name), a number, a name the admin may rename later (1D), and a type.

| No. | Key | Name | Type | Per party |
|---|---|---|---|---|
| 1000 | cash | Cash | asset | |
| 1010 | whish | Whish | asset | |
| 1100 | customers | Customers owing | asset | customer |
| 1110 | resellers | Resellers owing | asset | reseller |
| 1200 | upstream | Upstream provider credit | asset | upstream |
| 2000 | suppliers | Suppliers owed | liability | supplier |
| 2100 | salaries | Salaries owed | liability | employee |
| 3000 | opening | Opening balances | equity | |
| 3100 | money_in | Money put in (unclassified) | equity | |
| 3900 | corrections | Balance corrections | equity | |
| 4000 | income_subscriptions | Subscription income | income | |
| 4100 | income_resellers | Reseller income | income | |
| 5000 | cost_upstream | Upstream cost | expense | |
| 5100 | cost_payroll | Payroll | expense | |
| 5200 | discounts | Discounts and write-offs | expense | |
| 5900 | over_short | Cash over/short | expense | |
| 6xxx | expense:&lt;category id&gt; | one per expense category, named after it | expense | |

Reserved for later (not created yet): stock on hand, hardware sales, cost of hardware (Phase 2); owner drawings, loans, Whish fees (1D step 23).

"Per party" accounts carry the party on each line, so the account total is the control total and the per-party sum is that party's balance. A customer's credit is a credit balance on "Customers owing" (no separate account): one customer has one running balance, which matches how `Customer.balance` works today.

### Journal

- `journal_entry`: tenant, entry date (business-local day), created_at (UTC), created_by, memo, kind (`event`, `opening`, `correction`, `unexplained`).
- `journal_line`: entry, account, party type + party id (nullable), debit, credit (one of them > 0), source type + source id (the record that caused it, e.g. `payment` 812).
- Entries and lines are insert-only. No route, admin screen or AI tool can edit or delete them. Tenant delete removes them with the tenant.
- Every entry balances; this is checked when it is written (an unbalanced entry is a bug, see "Failure handling").

### The posting rule: desired state minus what is already posted

For each financial record, one pure function returns the lines the record **should** have contributed to the books in its current state (empty if deleted). When a record is created, changed or deleted, the listener compares that with what is already in the books for that record (its starting-point lines plus every line already posted for it) and posts only the difference.

This one rule covers create, edit, partial payment, split bills, method change (cash ↔ Whish), revert, re-pay, gratis, delete and bulk operations without special cases per route. A revert, for example, simply means "paid" is now false, so the receipt lines are no longer desired, and a correcting entry takes them out.

Lines posted for one transaction are grouped into one journal entry per entry date.

**Entry date:** the business-local day of the event. Customer receipts use the same date the cash reports use (`collected_at`, else `paid_at`), and bills use their bill date. Corrections (revert, delete, edit) are dated the day the correction happens, never back-dated into a closed day.

### What each record should contribute

Customer `Payment` (customers under a reseller never have these):
- **Bill**, any state except deleted: Dr Customers owing (customer) / Cr Subscription income, amount.
- **Bill paid with money** (paid, not gratis, not settled from credit, not reverted): additionally Dr Cash or Whish / Cr Customers owing. Whish if `collected_via` is `whish` or `whish_transfer`, else Cash.
- **Bill settled from credit:** the bill lines only; the customer's credit balance absorbs it, so no money moves.
- **Gratis:** the bill lines plus Dr Discounts and write-offs / Cr Customers owing.
- **Prepayment** (paid, not reverted): Dr Cash or Whish / Cr Customers owing.
- **Refund** (paid, not reverted): Dr Customers owing / Cr Cash or Whish.
- **Reverted** rows: the receipt / refund lines are no longer desired.

`ResellerPayment`:
- `credit_added`: Dr Resellers owing (reseller) / Cr Reseller income.
- `discount_applied`: Dr Discounts and write-offs / Cr Resellers owing.
- `payment_received`: Dr Cash or Whish (`paid_via`) / Cr Resellers owing.
- `payment_collected` (legacy, read-only, never created now): treated like `payment_received`.

`UpstreamProviderPayment`:
- `balance_topup`: Dr Upstream provider credit (provider) / Cr Cash or Whish.
- `renewal_cost`: Dr Upstream cost / Cr Upstream provider credit.

`SupplierPayment`: Dr Suppliers owed (supplier) / Cr Cash or Whish.

`Expense`:
- On credit from a supplier (`is_credit` with supplier): Dr the expense category / Cr Suppliers owed (supplier).
- Payroll payment (`employee_id`): Dr Salaries owed (employee) / Cr Cash or Whish.
- Otherwise: Dr the expense category / Cr Cash or Whish.

`SalaryCharge`: salary and bonus give Dr Payroll / Cr Salaries owed (employee); a deduction gives the reverse.
`SalaryPayment`: legacy, nothing creates it any more; no lines.
`CashEntry`: Dr Cash or Whish (`account`) / Cr Money put in (unclassified).
`AccountTransfer`: Dr the account it goes to / Cr the account it comes from.
`DayClose`: counted − expected per channel. A shortfall gives Dr Cash over/short / Cr Cash or Whish; a surplus gives the reverse. Reopening the day deletes the close, so the lines are taken out.

### Deleting a customer

`_delete_customer_core` (and anything else that removes a customer) cascade-deletes the customer's `Payment` rows. Money that was really received must not leave the books (owner decision). So when a customer is deleted:

- The customer's `Payment` rows are **not** run through the posting rule. Their receipts, refunds and income stay posted exactly as they were.
- One entry, dated the day of the delete, moves the customer's remaining balance on "Customers owing" to **Balance corrections**, memo "Customer deleted: &lt;name&gt;". That balance is what they still owed, or their credit.
- Reseller rows that keep the deleted customer's id are unaffected.

The deleting transaction tells the listener which customer is being deleted, by marking the session before the cascade. That is how the listener tells "customer deleted" apart from "one bill deleted".

### Fixing records from before the books started

A record older than the books only has starting-point lines; its income or expense was never in the books. It is summed up inside Opening balances. When such a record is changed or deleted later, the correction posts:

- its **balance-sheet side** normally: Cash, Whish, and the party accounts;
- its **profit side** (income and expense accounts) to **Opening balances**, as a prior-period adjustment.

Example: deleting a 2025 expense does not lower this month's expenses. The first months' figures stay free of fixes to history. *(Claude's call, standard accounting practice.)*

### Balance changes with no record behind them

These change a stored balance directly:
- hand-typed balance corrections (`_adjust_balance`, step 14);
- opening balances typed when creating a reseller or upstream provider;
- restoring lost credit (`resolve_credit_review`);
- the customer import.

Each such path posts explicitly: Dr/Cr the party account / Cr/Dr **Balance corrections**, with the reason as memo. They run through the one helper `_adjust_balance` where possible. The others are three small call sites.

**Safety net.** Within each transaction, the listener also compares each party's stored-balance change with the party lines it posted. Any difference (a path nobody mapped, now or in future code) is posted to **Balance corrections** as an entry of kind `unexplained`, with the party and the route name, and shows on the Books check. The books therefore always agree with stored balances, and anything unmapped is visible instead of silent.

### Starting the books (cut-over)

On the day the books go live, an admin action — or automatically on the first nightly run after deploy (see "Left to the builder") — does, per business, in one transaction:

1. Create the chart of accounts.
2. **Opening entry**, dated that day:
   - every customer, reseller, supplier, employee and upstream provider with a non-zero stored balance, against Opening balances;
   - Cash and Whish at the closing balance the Cash Flow page shows for the day before. If no opening balance was ever set on that page, the amounts are 0 and the Books view says so.
3. **Starting point.** For every existing financial record, store the lines it would contribute in its current state as starting-point lines in a separate table. They are not part of the books; they are what "already posted" means for records older than the books. Without this, paying an old open bill next week would book its income a second time.
4. Mark the books as started (with timestamp).

Records are only posted for businesses whose books have started.

### Nightly check

It runs after the existing nightly jobs. Per business:
- each party: stored balance vs that party's account total;
- **Cash and Whish:** the books' balance vs the closing balance the Cash Flow page shows for the same day;
- every entry balances;
- totals per account.

### What "the books and the reports agree" means

This is the condition for ending the shadow period. Switching the reports is a later step. All three must hold:

1. Every stored party balance matches the books (nightly check).
2. Cash and Whish match the Cash Flow page (nightly check).
3. **Cash-basis profit** worked out from the books matches today's profit for the same months.

The books record income when a bill is created (accrual). The cash-basis view instead follows each Cash/Whish line to the account on the other side of its entry:

- Cash in from "Customers owing" counts as subscription income.
- Cash in from "Resellers owing" counts as reseller income.
- Cash out to "Upstream provider credit", i.e. a top-up, counts as upstream cost, the same way today's profit treats it.
- Cash out to an expense category, "Suppliers owed" or "Salaries owed" counts as that cost.

The journal must therefore keep the counter-account on every money line, which a balanced entry already does. The cash-basis report itself is a 1D step. In 1C the comparison is a test and a check on the Books view. *(Claude's call.)*

The result is stored and shown on the Books view with the list of mismatches and `unexplained` entries. It changes no data. Its count must not grow (the same rule as step 0).

### Books view (admin)

A new tab **Finance → Books**, admin only (finance users get it when reports switch over):
- **Accounts:** each account with debit, credit and balance as of a chosen date (trial balance), grouped by type, with totals that must match.
- **Journal:** entries with date, memo, lines, source (linking to the record), and who. Filters: date range, account, party.
- **Check:** last nightly result; mismatched parties; unexplained entries.

No buttons that change anything.

### Failure handling

The books must never cost a payment. If computing or writing a record's lines raises, the listener:
- logs the error;
- posts nothing for that record;
- writes a `ledger_problem` row (record, error) in the same transaction, so the user's action still commits.

The safety net and the nightly check then show the gap. The one exception is an unbalanced entry produced by a mapping bug: the same `ledger_problem` path is used. It is never committed unbalanced.

### Performance

The nightly billing run creates many bills at once. New records need no "already posted" lookup. Changed records need one indexed lookup by (source type, source id). Lines are bulk-inserted per flush, like the History log. Indexes: `journal_line (tenant, account, entry date)`, `(source type, source id)`, `(party type, party id)`.

## Constraints

- Flask + SQLAlchemy in `app.py`. It sits next to the existing listeners:
  - `_stamp_tenant_id`;
  - `_log_balance_changes`;
  - `_balance_changes_as_increments`, which turns balance writes into SQL expressions, so stored-balance deltas must be read before it runs;
  - `_write_audit_log` (after_flush).
- Postgres in production (Coolify VPS), SQLite in tests. Migrations add new tables only; no ALTER on hot tables.
- Business timezone for every date (existing `_local_*` helpers).
- Alembic revision ids are random and the migration chain must keep a single head (see migration history).

## Left to the builder

- **When cut-over runs:** my call is automatically, once, on the first nightly run after deploy for every business, at that moment. A one-off admin button is the fallback. The cut-over timestamp is stored, and the Books view shows it.
- Table and column names, account numbering inside each range, and how expense-category accounts are created (on first use is fine).
- Whether the "desired lines" functions live in `app.py` or a new `ledger.py` module (new module preferred, given `app.py`'s size).
- How concurrent activity during cut-over is handled. It runs when traffic is lowest. Anything that slips through shows up as `unexplained` on the next check, which is acceptable.
- UI layout of the Books tab, following the existing Finance tab components.
- Test approach. Expected at minimum:
  - every event in the mapping above, with the books balancing after each;
  - revert, re-pay, delete, partial and split;
  - a pre-cut-over record changed after cut-over;
  - the safety net;
  - the nightly check;
  - a failure in the books not blocking a payment;
  - the full existing suite still passing.
