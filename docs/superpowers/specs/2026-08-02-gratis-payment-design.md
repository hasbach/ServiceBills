# Gratis (Free) Payments — Design

## Problem

Today a generated `Payment` has exactly two outcomes: it stays unpaid (shows as outstanding/overdue), or it gets marked paid via `PUT /api/payments/<id>/mark_paid`, which always adds the full `amount` to `customer.balance` and counts toward revenue in every report. There is no way to waive a payment (e.g. a promotional free cycle, compensation for an outage, a staff/family account) without either leaving it incorrectly showing as money owed, or marking it paid and overstating both the customer's balance and the business's revenue.

## Data Model

### `Payment.is_gratis` (new column)
`Boolean, nullable=False, default=False`. True once the payment has been waived rather than collected.

### `Payment.gratis_note` (new column)
`Text, nullable=True`. Optional free-text reason for the waiver. Kept as a **separate** column from the existing `Payment.reason` (which is already populated by the manual "Add Payment" flow, e.g. "Late fee") so marking a payment gratis never overwrites an existing, unrelated reason.

Both columns are added via an Alembic migration.

## Backend

### `PUT /api/payments/<int:payment_id>/mark_gratis`

New, dedicated endpoint (not a branch inside the existing `mark_payment_as_paid`, which already juggles collect/pay/partial-payment logic — gratis has different-enough semantics, i.e. no amount handling and no balance mutation, that bolting it on risks entangling that function further).

- `@jwt_required()`.
- Permission: same gate as the existing "pay" (confirm receipt) action — `admin` or `finance` role only; 403 otherwise. A plain `collector` cannot waive revenue.
- 404 if the payment doesn't exist for the caller's tenant (`tenant_query`).
- 400 if the payment is already `paid` (covers both a previously-collected payment and a payment already marked gratis).
- On success:
  ```python
  payment.paid = True
  payment.paid_at = datetime.utcnow()
  payment.is_gratis = True
  payment.gratis_note = data.get('note', '') or None
  payment.received_by_id = current_user.id  # who processed the waiver, for audit
  db.session.commit()
  ```
  `customer.balance` and `payment.amount` are **not** touched — no money changed hands, and the original billed amount is preserved for the audit trail (shown with a "GRATIS" badge in the UI rather than zeroed).

### Report queries

Four existing queries sum `Payment.amount` filtered by `Payment.paid == True` as revenue; each gets one added filter, `Payment.is_gratis == False`, so waived payments no longer inflate revenue:

- `GET /api/reports/total-sales` (`app.py` ~L2382)
- `GET /api/reports/monthly-revenue` (`app.py` ~L3060, the sales half of the query)
- `GET /api/reports/revenue` (`app.py` ~L4522)
- `GET /api/reports/financial` (`app.py` ~L5169, the income half of the query)

`GET /api/reports/unpaid-payments` needs no change — a gratis payment has `paid == True` and already stops appearing there.

### Serialization

`Payment.to_dict()` (or wherever a payment is serialized for `GET /api/payments`) gains `is_gratis` and `gratis_note` fields so the frontend can render the badge and know when to hide/disable the "Mark Gratis" action.

## Frontend (`PaymentsView.js`)

- `apiService.markPaymentGratis(paymentId, note)` → `PUT /payments/:id/mark_gratis`, body `{ note }`.
- A new "Mark Gratis" icon button next to the existing "Mark Paid" action on each unpaid row, visible/enabled only for admin/finance (mirroring how the existing "pay" action is already gated client-side).
- Clicking opens a small dialog (same shape as the existing `markPaidDialog`): customer name, original amount (read-only), an optional "Reason (optional)" text field, and a "Confirm Gratis" button. No amount input — the customer isn't charged anything.
- On success, refetch payments (same `fetchAll()`/`fetchPayments()` pattern already used after "Mark Paid").
- Any row where `payment.is_gratis` is true shows the original amount plus a distinct "GRATIS" chip next to the existing paid/status chip. The "Mark Gratis" and "Mark Paid" actions are both hidden once a payment is settled (paid or gratis), matching how "Mark Paid" already disappears once `payment.paid` is true.

## Out of scope (explicitly deferred)

- Bulk "Mark Gratis" (multi-select) — only a single-payment action for now.
- Marking a payment gratis at generation/creation time — only for an already-existing payment record.
- Receipts for gratis payments — `generate_receipts_for_month` and the single-receipt fetch flow are untouched; a gratis payment simply won't have a receipt generated. Can be revisited later.

## Testing

New backend tests (alongside the existing `test_bulk_actions.py`/payment tests):

1. Marking gratis sets `paid=True`, `is_gratis=True`, `gratis_note` stored, `payment.amount` unchanged, and `customer.balance` **unchanged**.
2. A gratis payment is excluded from `/api/reports/monthly-revenue` and `/api/reports/financial` income, but still appears in a plain `GET /api/payments` list.
3. A non-admin/finance role (e.g. plain `collector`) gets 403.
4. Marking an already-paid (or already-gratis) payment gratis again returns 400.
5. Tenant isolation: cannot mark another tenant's payment gratis (404, via existing `tenant_query` scoping).
