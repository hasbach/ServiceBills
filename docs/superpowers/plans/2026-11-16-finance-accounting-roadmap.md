# Finance accuracy, accounting core and inventory — roadmap

Date: 2026-11-16. Status: **1A and 1B are merged and live. 1C (the books) is built on branch `feat/finance-ledger-1c`, not yet merged. Nothing further starts until the owner confirms.**

Source: the finance review of 2026-11-16 (payments, reports/cash flow, suppliers/payroll/resellers).

## Ground rules

- **USD only.** LBP is not used. No currency work is planned; the existing `currency` / `fx_rate_to_reporting` columns stay untouched and are treated as always USD / 1.
- **Isolated worktree.** All building and fixing happens in a separate git worktree branched from `main`, never in the main checkout. One branch and one pull request per step below.
- **Small steps, each with tests.** Every step ships on its own, with tests that reproduce the bug or prove the feature before it is merged.
- **No data repair.** Fixes stop new damage. Existing balances are left exactly as they are (owner decision 2026-11-16); the read-only check (step 0) is for information and regression testing only.
- **Inventory is Phase 2**, but Phase 1 is designed so inventory plugs in without rework (see "What Phase 1 prepares for inventory").
- The AI connector write tools (PR #59, not merged) call the same routes, so they inherit every fix; they need re-testing after steps 1A and 1B.

## Phase 0 — Measure before touching (read-only)

**0. Reconciliation check.** A read-only report, per tenant, that recomputes each customer, reseller, supplier and employee balance from its history and lists every one that disagrees with the stored balance, plus payments refunded more than once and bills marked paid with no money behind them. Changes no data. It shows how much disagreement exists and becomes the regression check for everything after (the count must never grow). No balance is corrected from it.

## Phase 1 — Finance

### 1A. Accuracy fixes (wrong figures today)

Each is a separate small fix.

| # | Fix | Where |
|---|-----|-------|
| 1 | Revert/delete of a bill paid from customer credit must restore the credit, not remove it twice; clear the "paid from credit" mark on revert | `app.py` 6914, 6021, 7137 |
| 2 | Revert-then-delete of a prepayment must not leave phantom credit | 6022–6025, 7138 |
| 3 | Refund limit: total refunds can never exceed the original payment; refunds linked to the original; no refund of gratis bills, credit-settled bills or refund rows | 6967–6981 |
| 4 | Moving a customer off a reseller: only un-collected charges move, not the whole history | 5143–5162 |
| 5 | Moving a customer onto a reseller: open bills are transferred, not marked "paid" (no phantom revenue) | 5167–5171 |
| 6 | Profit includes reseller income and upstream provider cost | 12053–12111, 9261–9293, 7583 |
| 7 | Day lock holds for payments that were reverted and re-paid; "collect" refused on an already-paid payment | 11432, 6297 |
| 8 | Salary accrual: start from an explicit "accrue from" date, never back-bill years or inactive months; one salary charge per employee per month enforced | 3871–3901 |
| 9 | One definition of "revenue" used by dashboard, Payments page, Reports and Finance, with month and day boundaries in the business timezone | 9261, 10508, 6061, `PaymentsView.js` |
| 10 | Billing schedule not shifted by refunds or manually dated charges | 3798–3803 |
| 11 | Amount validation on expenses and all money-out routes (no negative, zero or invalid amounts) | 10964, 11027 |

### 1B. Control and traceability

| # | Step | What changes for the user |
|---|------|---------------------------|
| 12 | **Void instead of delete** for payments, expenses, supplier payments, salary charges, cash entries, transfers | A voided record stays visible, struck through, with who/when/why. Nothing financial is ever physically deleted. |
| 13 | **Audit log** for every financial create / edit / void / day reopen | A "History" view: who, when, old value, new value. |
| 14 | **Balance adjustments as entries** | "Fix balance" and manual balance edits (customer, reseller, supplier, employee, upstream) become a dated adjustment line with a mandatory reason. |
| 15 | **Correct reseller collections and upstream top-ups** | Edit / void buttons, same rules as supplier payments. |
| 16 | **Double-submit and concurrency protection** | A double click or two users at once can no longer create two payments or lose an update. |
| 17 | **Receipt numbers** | Sequential per business, issued when paid, never reused; a receipt cannot be regenerated with different figures. |
| 18 | **Permissions** | Financial reports limited to admin and finance; expense categories limited the same way. |

### 1C. Accounting core (ledger)

**Status:** built on branch `feat/finance-ledger-1c` (not yet merged). Design: `docs/superpowers/specs/2026-11-17-ledger-design.md`. Plan: `docs/superpowers/plans/2026-11-17-ledger-books.md`.

The step that makes it "professional accounting software". Recommended design:

- **Chart of accounts**, created automatically and editable: Cash, Whish (no bank account); Customers owing (receivables); Resellers owing; Customer credit; Suppliers owed (payables); Salaries owed; Upstream providers; Owner capital / drawings; Income (subscriptions, resellers, hardware sales, other); Costs (upstream, payroll, each expense category, stock used); Cash over/short.
- **Journal**: every money event writes balanced entries (double-entry) that can never be edited, only reversed by a new entry. Users keep using the same screens; the journal is written behind them.
- **Balances come from the journal.** Stored balances remain as a fast copy, checked against the journal automatically every night; any mismatch is flagged instead of hidden.
- **Cut-over**: the ledger starts on an agreed date with opening balances equal to the stored balances on that date, as they are. Older periods stay as they are today.
- **Shadow period**: the ledger runs alongside the current reports until both agree, and only then do the reports switch to it.

### 1D. Reports and features on the ledger

| # | Feature |
|---|---------|
| 19 | Profit and loss (income, upstream cost, payroll, expenses by category), monthly and for any range |
| 20 | Balance summary / trial balance: cash, Whish, who owes you, whom you owe |
| 21 | Aging: customers and resellers owing (0–30 / 31–60 / 61–90 / 90+), and what you owe suppliers |
| 22 | Statement with running balance for every supplier, reseller, employee and upstream provider (customers already have one) |
| 23 | Money movements that are not expenses: owner drawings, capital put in, loans, Whish fees |
| 24 | Supplier bills: bill number, date, due date, lines, attachment; payments are allocated to bills |
| 25 | Month close (lock a month, admin reopens with a recorded reason), on top of the existing day close |
| 26 | Excel and PDF export of every financial report and statement |
| 27 | Attachments on expenses; recurring expenses (rent, subscriptions) |

### What Phase 1 prepares for inventory

- Supplier bills (step 24) are built with lines, so a line can later point at a stock item.
- The chart of accounts reserves "Stock on hand", "Hardware sales" and "Cost of hardware sold / used".
- Document numbering (step 17) is one shared mechanism, reused for sales invoices.
- Void / audit / month-lock rules (steps 12, 13, 25) apply to stock documents automatically.

## Phase 2 — Inventory module

- **Items**: name, category (ONU, router, cable, splitter, …), unit, sale price, minimum stock level. Quantities only, no serial numbers; one stock location.
- **Purchases**: a supplier bill with item lines adds stock at cost and either adds to what you owe the supplier or is paid from Cash / Whish.
- **Sales invoice**: numbered invoice to a customer (or walk-in) with item lines; reduces stock, adds to the customer's balance, is paid through the existing Receive Payment; hardware income and its cost appear in profit and loss.
- **Used in maintenance**: issue items to a job, linked to the customer, ticket or network device; booked as a maintenance cost at purchase cost, optionally billed to the customer.
- **Replacement / swap**: one action issues the new item and takes the old one back as "returned – usable", "returned – faulty" or "scrapped", with the customer's device record updated.
- **Other movements**: stock count adjustment, loss / damage, return to supplier, return from customer.
- **Costing**: weighted average cost per item (recommended; simple and standard).
- **Reports**: stock on hand and its value, movement history per item, low-stock list, hardware profit, items used per customer.
- Every stock movement posts to the ledger, so stock value, cost and profit always agree with the finance reports.

## Order of work

0 → 1A (fixes 1–11) → 1B (12–18) → 1C (ledger, shadow period) → 1D (19–27) → Phase 2.

1A and 1B are valuable on their own and do not depend on the ledger decision. 1C is the largest single step and the one that carries the most risk; it is done with the old reports still live beside it.

## Decisions (owner, 2026-11-16)

1. **Ledger** — build the double-entry journal described in 1C.
2. **History** — the ledger starts from a cut-over date with opening balances; past data is not rebuilt.
3. **Old balances** — left as they are. No corrections from step 0.
4. **Profit basis** — cash basis stays the default; an "earned / owed" view is added once bills and invoices exist.
5. **Bank account** — not needed. Money accounts are Cash and Whish only.
6. **Inventory** — one stock location, no serial numbers.

Still required before any work starts: the owner's go-ahead to begin building.
