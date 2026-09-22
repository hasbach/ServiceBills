# Daily Cash Report — Design

**Status (2026-09-22): all open questions answered by the business owner during brainstorming. Ready for an implementation plan.**

## What this is

A daily cash-reconciliation report: for a given calendar day, how much cash each field collector collected (and how much was paid directly at the office), so it can be checked against what's physically handed in at end of day. This is distinct from the existing `/api/reports/collector-progress` (date-range progress over a period) and `/api/reports/financial` (month-by-month income/expense) — this report is single-day and specifically about **cash in hand**, not digital totals.

## Non-goals

- **No new `payment_method` field.** The owner confirmed the existing `Payment.collected_via` (`None` | `'whish'`) is sufficient — every non-Whish payment is treated as cash. If a genuine non-cash manual method (bank transfer, check) is introduced later, that's a separate change.
- **No export (CSV/print)** in v1 — not requested.
- **No per-collector login restriction** in v1 — every admin/finance user sees every collector's totals. Restricting a collector's own login to their own row is a possible future change, not built here.
- **No new database table/model.** This is a live query over existing `Payment` rows, following the same pattern as `get_collector_progress`/`get_financial_report`.
- **No changes to how `collected_via`, `collected_by_id`, or `received_by_id` are set** — this report only reads existing data.

## Backend: `GET /api/reports/daily-cash`

New route in `app.py`, placed alongside the other `/api/reports/*` routes (near `get_collector_progress` at `app.py:8791`).

**Query params**: `start_datetime` and `end_datetime` — full ISO instants marking the selected day's local-midnight-to-next-local-midnight window, exactly as `get_collector_progress` already accepts `start_date`/`end_date` (`app.py:8795-8803`). The frontend computes these from the browser's local timezone (see Frontend section) — the backend does no timezone math of its own, avoiding a hardcoded Beirut UTC offset that would be wrong for a different deployment or a future DST change. This mirrors how `formatStamp.js` already fixed the "3-hours-behind UTC" display bug by trusting the browser's local timezone rather than hardcoding an offset server-side.

**Auth**: `@jwt_required()` plus the same role check `mark_payment_gratis` uses (`app.py:4963-4965`) — `admin` or `finance` only:

```python
roles = [r.strip().lower() for r in current_user.role.split(',')]
if 'admin' not in roles and 'finance' not in roles:
    return jsonify({'message': 'Unauthorized. Only finance or admin can view the daily cash report.'}), 403
```

**Query**, filtered to the current tenant (`current_tenant_id()`) and the selected window on `COALESCE(Payment.collected_at, Payment.paid_at)`:

```python
payments = tenant_query(Payment).filter(
    Payment.paid == True,
    Payment.collected_via.is_(None),          # cash: not Whish
    Payment.is_gratis == False,
    Payment.is_refund == False,
    Payment.reverted_at.is_(None),
    func.coalesce(Payment.collected_at, Payment.paid_at) >= start_datetime,
    func.coalesce(Payment.collected_at, Payment.paid_at) < end_datetime,
).all()
```

Grouped in Python (small daily row count, no need to push grouping into SQL) by `payment.collected_by_id or payment.received_by_id`:

- A group with a `collected_by_id` is labeled by that `User.username` (a field collector).
- A group with **no** `collected_by_id` but a `received_by_id` is labeled `"Office / Direct"` — money paid straight to the office rather than physically collected by a field agent.
- Each payment's contribution to the group total is `amount * fx_rate_to_reporting` (same conversion `get_collector_progress`/`get_financial_report` already use), so mixed-currency cash rolls into one reporting-currency total per group. A payment with a `fx_rate_to_reporting` of `None` (shouldn't happen post multi-currency migration, but defensively) is included at its raw `amount` and flagged `rate_missing: true` in its payment entry, so the frontend can visually call it out instead of silently mis-totaling.

**Response shape**:

```json
{
  "date_start": "...",
  "date_end": "...",
  "grand_total": 1234.56,
  "reporting_currency": "USD",
  "groups": [
    {
      "collector_id": 7,
      "collector_name": "jad",
      "is_office": false,
      "total": 800.0,
      "payment_count": 12,
      "payments": [
        {"id": 501, "customer_name": "...", "amount": 65.0, "currency": "USD",
         "reporting_amount": 65.0, "rate_missing": false,
         "time": "2026-09-22T14:03:00"}
      ]
    },
    {
      "collector_id": null,
      "collector_name": "Office / Direct",
      "is_office": true,
      "total": 434.56,
      "payment_count": 5,
      "payments": [ ... ]
    }
  ]
}
```

`reporting_currency` comes from the tenant's existing reporting-currency setting (same source `get_financial_report` already reads, per the multi-currency spec).

## Frontend: new "Daily Cash" tab in `EnhancedReportsView.js`

- A date picker (MUI, matching the existing report tabs' style), defaulting to today.
- On change/load, the picker's selected calendar date is converted to a local-midnight `Date` and a next-local-midnight `Date` (`new Date(y, m, d, 0, 0, 0)` / `+1 day`), each sent via `.toISOString()` as `start_datetime`/`end_datetime`. This is what makes the report use the *browser's* local day boundary (Beirut, for this app's actual users) without the backend needing to know a timezone.
- A grand-total banner at the top (`grand_total` + `reporting_currency`).
- One row per group, sorted by `total` descending with "Office / Direct" always last regardless of its total (it's a different kind of bucket, not another collector to rank), showing name + total + payment count, expandable (existing expand/collapse pattern already used elsewhere in this file) to a table of that group's individual payments: customer name, amount (original currency), reporting amount, time. A payment with `rate_missing: true` is visually flagged (e.g. a warning icon/tooltip) rather than silently included.
- Empty state: a day with zero cash payments shows "No cash payments collected on this day" rather than an empty table.

## Testing

- Backend test seeding: two collectors with cash payments, one office-direct payment (`received_by_id` set, `collected_by_id` null), one Whish payment (`collected_via='whish'`, must be excluded), one refunded payment (`is_refund=True`, excluded), one gratis payment (excluded), one reverted payment (`reverted_at` set, excluded), and payments just inside/outside the day window (boundary test) — asserting correct grouping, exclusions, and totals.
- A payment with `fx_rate_to_reporting=None` (defensive path) — asserts it's included with `rate_missing: true` rather than raising.
- Role check: a non-admin/finance user gets 403.
- Tenant isolation: a payment belonging to another tenant never appears (standard `tenant_query`/`current_tenant_id()` scoping, verified the same way other report tests already do).
- Manual check against the live dev DB with real payment data before shipping, per this project's established pattern of verifying reports against real data rather than trusting tests alone.
