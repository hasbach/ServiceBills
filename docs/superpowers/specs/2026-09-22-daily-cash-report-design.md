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

**Query params**: `start_date` and `end_date` — the same param names `get_collector_progress`/`get_revenue_report` already use, but here carrying the selected day's exact local-midnight-to-next-local-midnight instants (not date-only strings) — parsed the same way `get_revenue_report` already does (`app.py:8096-8097`: `datetime.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)`), with no backend-side end-of-day reinterpretation (unlike `get_collector_progress`, which forces `end_date` to `23:59:59` — this endpoint must NOT do that, since the frontend already sends the exact exclusive boundary). The frontend computes these from the browser's local timezone (see Frontend section) — the backend does no timezone math of its own, avoiding a hardcoded Beirut UTC offset that would be wrong for a different deployment or a future DST change. This mirrors how `formatStamp.js` already fixed the "3-hours-behind UTC" display bug by trusting the browser's local timezone rather than hardcoding an offset server-side.

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
    func.coalesce(Payment.collected_at, Payment.paid_at) >= start_date,
    func.coalesce(Payment.collected_at, Payment.paid_at) < end_date,
).all()
```

Grouped in Python (small daily row count, no need to push grouping into SQL) by `payment.collected_by_id or payment.received_by_id`:

- A group with a `collected_by_id` is labeled by that `User.username` (a field collector).
- A group with **no** `collected_by_id` but a `received_by_id` is labeled `"Office / Direct"` — money paid straight to the office rather than physically collected by a field agent.
- Each payment's contribution to the group total is `amount * fx_rate_to_reporting` (same conversion `get_collector_progress`/`get_financial_report` already use), so mixed-currency cash rolls into one reporting-currency total per group. `Payment.fx_rate_to_reporting` is a `NOT NULL` column defaulting to `1` (`app.py:1110`), so there is no "missing rate" case to defend against — an opted-out (single-currency) tenant's payments are always `amount * 1`.

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
         "reporting_amount": 65.0,
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

## Frontend: new "Daily Cash Report" option in `EnhancedReportsView.js`

This file's report chooser is a `Select`/`MenuItem` dropdown (`reportType` state), not tabs — a new `<MenuItem value="daily-cash">Daily Cash Report</MenuItem>` is added alongside the existing options (`frontend/src/components/EnhancedReportsView.js:373-378`).

- A single date picker, defaulting to today (reusing the existing `startDate` state; the existing `endDate` picker is hidden while `reportType === 'daily-cash'`, since this report has no range — see Frontend Details below for exactly how).
- On change/load, the picker's selected calendar date is converted to a local-midnight `Date` and a next-local-midnight `Date` (`new Date(y, m, d, 0, 0, 0)` / `+1 day`), each sent via `.toISOString()` as `start_date`/`end_date`. This is what makes the report use the *browser's* local day boundary (Beirut, for this app's actual users) without the backend needing to know a timezone.
- A grand-total banner at the top (`grand_total` + `reporting_currency`).
- One row per group, sorted by `total` descending with "Office / Direct" always last regardless of its total (it's a different kind of bucket, not another collector to rank), showing name + total + payment count, expandable (MUI's standard collapsible-table-row pattern — `Collapse` + an expand/collapse `IconButton` per row — this file has no existing expand/collapse to reuse, so this introduces the pattern fresh) to a table of that group's individual payments: customer name, amount (original currency), reporting amount, time.
- Empty state: a day with zero cash payments shows "No cash payments collected on this day" rather than an empty table.

## Testing

- Backend test seeding: two collectors with cash payments, one office-direct payment (`received_by_id` set, `collected_by_id` null), one Whish payment (`collected_via='whish'`, must be excluded), one refunded payment (`is_refund=True`, excluded), one gratis payment (excluded), one reverted payment (`reverted_at` set, excluded), and payments just inside/outside the day window (boundary test) — asserting correct grouping, exclusions, and totals.
- Role check: a non-admin/finance user gets 403.
- Tenant isolation: a payment belonging to another tenant never appears (standard `tenant_query`/`current_tenant_id()` scoping, verified the same way other report tests already do).
- Manual check against the live dev DB with real payment data before shipping, per this project's established pattern of verifying reports against real data rather than trusting tests alone.
