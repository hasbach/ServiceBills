# Receive payment, customer statement, and the credit-settlement fix

Date: 2026-10-05 · Status: approved (owner: "use the recommended answer" for open questions)

## Problem

1. Staff settle a customer's money bill by bill. When a customer hands over $30 against two $25 bills,
   staff must fully pay one card, then partial-confirm the other, sometimes with a separate collect step
   and confirm step for each card.
2. **Production bug (DeltaNet tenant, owner-confirmed).** A customer had +$25 credit. On renewal the
   balance went to $0 but the new $25 bill card stayed **unpaid**.
3. There is no per-customer history of how the balance moved.

## Root cause of (2)

`Customer.balance` is the customer's **net position**. Every path that creates an unpaid bill subtracts
the bill's amount in the same transaction, and confirming a bill adds it back (the invariant is
documented in `tests/test_tenant_wide_payment_page.py`). So money received but not yet matched to a
bill equals `balance + sum(unpaid bills)`, not `balance`.

`apply_customer_balance_to_unpaid_payments` treats `balance` itself as the unmatched credit:

| Before | Old behaviour | Correct |
|---|---|---|
| credit 25, bill 25 → balance 0 | `balance <= 0`, so nothing happens and the bill stays unpaid | bill paid, balance 0 |
| credit 50, bill 25 → balance 25 | bill paid **and** balance −25 → **0** (customer loses $25) | bill paid, balance 25 |
| credit 60, bills 25+25 → balance 10 | bill A split 10 paid / 15 unpaid, balance 0, bill B unpaid | both paid, balance 10 |

Both symptoms were reproduced against the old code. The daily scheduler (`generate_missing_payments`)
calls this function for **every active customer**, so the second row has been taking real credit away
in production whenever a customer's credit exceeded their open bills.

## Design

### 1. Fixed credit settlement (`apply_customer_balance_to_unpaid_payments`)
- `credit = balance + sum(all unpaid bills)`.
- Settle the oldest unpaid bills that are **not** in a collector's hands (`collected=False`), splitting
  the last one if the credit covers only part of it. Bills a collector already holds cash for are left
  to that collection.
- **The balance is not changed.** It already reflects the credit and the bills.
- Settled rows get the new `Payment.settled_from_credit = True`. Like before, they carry no
  `received_by`/`collected_by`, so the Daily Cash report keeps excluding them (no cash moved).
- Split remainders copy currency, FX rate, date and reason. The old split code dropped currency/FX.

**Repair of stale cards:** none needed beyond deploying. The scheduler re-runs this function daily for
active customers, so cards already covered by credit (the DeltaNet case) are settled on the first run
after deploy. Receive payment (below) runs it as well.

### 2. Lost-credit review (repair for the double deduction)
A row the old function settled looks like this: `paid=True`, not a prepayment, gratis or refund, not
reverted, no `received_by_id`/`collected_by_id`/`collected_via`, and `settled_from_credit=False`.
`received_by_id` has existed since the first commit, so this pattern reliably means "auto-settled by
the old code". **Each such row took its amount off the balance once too often.**

- `GET /api/customers/credit-review` (admin/finance) lists affected customers: their bills, amounts,
  dates and the suggested credit-back.
- `POST /api/customers/<id>/credit-review` with `{action: 'restore'|'dismiss'}`:
  - **restore** adds the sum back to the balance and runs settlement.
  - **dismiss** marks the rows reviewed without changing anything (e.g. an admin already fixed the
    balance by hand).
  - Both stamp `Payment.credit_review` (`'restored'`/`'dismissed'`) so a row is never offered twice.
- **Not automatic.** It changes money, and some balances may already have been corrected by hand. The
  admin decides per customer.

### 3. Receive payment (one amount for a customer)
`POST /api/customers/<id>/receive-payment`
`{amount, action: 'pay'|'collect', method: 'cash'|'whish_transfer', reference?, first_payment_id?}`
- **pay** (admin/finance). Applied oldest bill first, starting with `first_payment_id` if given (the
  card the user clicked). Each fully covered bill is marked paid exactly as "Confirm Receipt" does
  (`received_by` = user). The last bill is split if only partly covered. Anything left over becomes a
  paid prepayment row (credit), with FX locked like `add_payment`. Settlement and the Mikrotik restore
  run afterwards.
- **collect** (collector, cashier, admin, finance; same rule as `mark_paid` collect). Applied across
  unpaid bills not already collected. Fully covered bills get `collected_amount = amount`; the last one
  gets the partial amount. **No splitting at collect time**, matching per-card collect today. An amount
  above what the customer owes is rejected (a collector can't create credit; the extra is taken at the
  office). One WhatsApp `payment_paid` notification is sent for the whole amount.
- `whish_transfer` sets `collected_via='whish_transfer'` (and the reference) on every row touched, so
  the cash report keeps it out of cash.

`POST /api/customers/<id>/confirm-collected` (admin/finance) confirms every collected-but-unconfirmed
bill of the customer in one step: full when `collected_amount >= amount`, otherwise split, the same as
partial confirm today.

### 4. Customer statement (balance log)
`BalanceLog` gains `customer_id`. The existing `before_flush` listener now also logs `Customer` balance
changes. Reasons come from the Payment rows in the same flush:
- new bill → "Bill: …"
- prepayment → "Payment received (credit)"
- bill paid → "Payment received for bill of …"
- gratis → "Bill forgiven"
- revert → "Payment reverted"
- refund → "Refund"
- otherwise "Balance edited manually"

Receive payment and the review supply their own reasons. For speed in the scheduler's large flushes,
Payment rows are indexed by customer once per flush. `GET /api/customers/<id>/balance-log`. Not
backfilled.

### 5. UI
- **Subscriptions → Payments History dialog:** a **Receive payment** button (Pay or Collect according to
  role, amount, method), plus tabs **Bills** (today's table) and **Statement** (`BalanceLogTable`).
- **Payments page:**
  - The Collect / Confirm dialog accepts more than the card's amount. The rest goes to the customer's
    other bills (then credit, for pay only), via `receive-payment` with `first_payment_id`.
  - Confirming a collected card offers "also confirm this customer's other collected payments (total
    $X)", checked by default, via `confirm-collected`.
  - Admins see a **Credit check (N)** button when the review has rows.

## Decided during implementation
- **Revenue excludes `settled_from_credit` bills** (dashboard, total sales, monthly revenue, revenue report
  and financial report). The prepayment that created the credit is already counted as income, so
  counting the bill again double-counts it. This surfaced once settlement started working.
- **Whish leftover now settles part of the next bill.** `_apply_whish_debt_then_prepayment` still pays
  whole bills only. Its leftover prepayment then flows through settlement, which may split the next
  bill, so the open cards always add up to what the balance says is owed. The old test asserting "never
  partially marks" was rewritten for this.
- **Statement reasons survive autoflush.** Ledger rows seen in any flush are kept in `session.info`
  until the owner's balance change is logged, and dropped on commit/rollback.
- **Receive payment resolves FX before moving money.** `get_tenant_settings` may commit (when it creates
  a missing settings row), so the credit row's currency and rate are looked up before any bill is
  touched.
- **Credit review** also requires `paid_at IS NOT NULL` and `collected IS NOT TRUE`. Moving a customer
  under a reseller closes bills as collected/0 with nobody recorded; that is a debt transfer, not lost
  credit.
- **Payments page bug fixed in passing:** grid cards held a stale `openMarkPaidDialog` closure from the
  first render. It now goes through the same ref-backed wrapper the list view uses.

## Not in Phase 1
- A separate receipt + allocation model, so partial payments stop mutating the bill row (Phase 2).
- Receipts for a lump sum (each settled bill keeps its existing receipt flow).
- Backfilling the statement.
