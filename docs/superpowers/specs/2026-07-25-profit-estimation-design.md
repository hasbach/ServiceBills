# Estimated vs. Real Monthly Profit — Design

## Problem

Today "profit" in this app is purely cash-basis and retrospective: `/api/reports/financial` sums paid `Payment` income minus non-credit `Expense` + `SupplierPayment` + `SalaryPayment` cash outflows for a month, after the fact. There is no notion of *cost* per subscription, so there is no way to know in advance what a month's profit *should* look like — and no way to compare that estimate against what actually happened once the month closes.

The business need: model a cost per subscription (which can vary per customer even on the same plan — e.g. plan "Fiber 50" nominally costs $15 to deliver, but a specific customer's real underlying connection costs $18), derive an estimated monthly profit from currently active subscriptions, log that estimate per month, and compare it against the real (cash-basis) profit for the same month.

## Data Model

### `SubscriptionPlan.cost` (new column)
`Float, nullable=False, default=0.0`. The plan's default cost — what it typically costs the business to deliver this plan, distinct from `price` (what the customer is charged).

### `Customer.cost_override` (new column)
`Float, nullable=True`. When set, this customer's actual cost overrides the plan default (handles the "same plan, different real cost" case from the problem statement). `NULL` means "use `subscription_plan.cost`." This exactly mirrors the existing `Customer.discount` field, which already overrides `subscription_plan.price` per customer — cost gets the same per-customer override mechanism as price already has.

### `MonthlyProfitEstimate` (new table)
One row per tenant per calendar month — the logged estimate.

```python
class MonthlyProfitEstimate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)  # 'YYYY-MM'
    estimated_income = db.Column(db.Float, nullable=False, default=0.0)
    estimated_cost = db.Column(db.Float, nullable=False, default=0.0)
    estimated_profit = db.Column(db.Float, nullable=False, default=0.0)  # denormalized: income - cost
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('tenant_id', 'month', name='uq_monthly_profit_estimate_tenant_month'),)
```

Registered in `TENANT_OWNED_MODELS` like every other tenant-scoped table.

**Freezing by time passing:** this row is only ever upserted for the *current* calendar month (see Computation below). Once the calendar rolls into a new month, nothing in the system ever targets last month's row again — it is never explicitly "closed," it simply stops being written to, which makes it a stable historical record without needing a separate close/lock step.

## Computation

### Per-customer monthly margin

```
effective_price = max(0, subscription_plan.price - customer.discount)       # existing formula
effective_cost  = customer.cost_override if customer.cost_override is not None else subscription_plan.cost

contribution = (effective_price - effective_cost)         if billing_cycle == 'monthly'
             = (effective_price - effective_cost) / 12     if billing_cycle == 'yearly'
```

Yearly plans are amortized over 12 months so a monthly estimate is comparable across customers regardless of billing cycle, and comparable to the cash-basis real profit (which is also inherently monthly). No proration for mid-month signups/cancellations — a customer active at all during the month counts as a full month's contribution, consistent with the rest of the app (billing itself is not prorated either). A customer whose plan has a `billing_cycle` other than `'monthly'`/`'yearly'` is skipped (contributes `0`), matching `generate_missing_payments`'s existing `continue`-on-unrecognized-cycle behavior.

### `recalculate_estimated_profit(tenant_id)`

Sums `effective_price`, `effective_cost`, and `contribution` across all of the tenant's `Customer` rows with `is_subscription_active == True`, for the **current** calendar month (`datetime.utcnow().strftime('%Y-%m')`), and upserts the `MonthlyProfitEstimate(tenant_id, month)` row (`estimated_income`, `estimated_cost`, `estimated_profit = income - cost`).

This function takes an explicit `tenant_id` and never calls `tenant_query(...)`, so it's safe to call both from request handlers (where a JWT tenant is available) and from the scheduler (where it isn't) — the same convention already used by `generate_missing_payments` and `generate_missing_salary_charges`.

### Triggers (call `recalculate_estimated_profit(tenant_id)` after commit)

- `add_customer` — new subscription
- `update_customer` — `discount`, `cost_override`, or `subscription_plan_id` changed
- Customer delete
- Subscription activate/deactivate (`is_subscription_active` toggled)
- `add_subscription_plan`
- `update_subscription_plan` — `price` or `cost` changed

### Daily scheduler safety net

A third job on the existing `BackgroundScheduler` (same `interval`/`days=1` trigger, same `RUN_SCHEDULER` gate as the billing and payroll jobs): `recalculate_all_estimated_profits_with_context()` iterates `Tenant.query.filter_by(status="active")` and calls `recalculate_estimated_profit(t.id)` for each. This guarantees every tenant's current-month row exists and stays accurate even during a calendar month with zero triggering events (no signups, cancellations, or edits) — a corner case the event triggers alone wouldn't cover.

## API Changes

- `SubscriptionPlan.to_dict()` and the plan create/update routes gain `cost` (accepted on `POST`/`PUT /api/subscription_plans`, defaults to `0.0`).
- `Customer.to_dict()` and the customer create/update routes gain `cost_override` (accepted on `POST`/`PUT /api/customers`, nullable — omit or `null`/`''` clears it back to "use plan cost").
- `GET /api/reports/financial` (existing endpoint, extended in place, not replaced): for each `monthly_data` entry in the requested date range, join in the matching `MonthlyProfitEstimate` row by `(tenant_id, month)` and add:
  - `estimated_profit`: the logged value, or `null` if no row exists for that month (e.g. a month before this feature existed, or a quiet month the daily job hasn't reached yet).
  - `variance`: `profit - estimated_profit`, computed server-side; `null` when `estimated_profit` is `null`.
  - `totals.estimated_profit` / `totals.variance`: summed over whichever months in the range actually have a value (months with `null` don't contribute `0` — they're excluded from the sum, not counted as a zero estimate).
  - **Current-month lazy backfill:** if the requested range includes the current month and no `MonthlyProfitEstimate` row exists for it yet, the endpoint calls `recalculate_estimated_profit(tenant_id)` inline before building the response, so the current month is never blank even before the first trigger or daily tick fires.

No new dedicated CRUD endpoints for `MonthlyProfitEstimate` — it's written only by `recalculate_estimated_profit` and read only through the extended financial report endpoint.

## Frontend Changes

- **Subscription Plan form:** add a "Cost" input alongside the existing Price and Billing Cycle fields.
- **Customer form:** add an optional "Cost Override" input near the existing Discount field. Blank means "use the plan's cost."
- **Financial Report** (`EnhancedReportsView.js`, `renderFinancialView`):
  - A 4th bar, "Estimated Profit," on the existing income/expenses/profit `BarChart`.
  - A 4th summary card, "Estimated Profit," alongside the existing Total Income/Expenses/Profit cards.
  - A 4th column, "Estimated Profit," in the monthly breakdown table.
  - A 5th column, "Variance" (`real − estimated`), color-coded green (real profit beat the estimate) or red (fell short).
  - Months with `estimated_profit: null` render as `—` in both the table and variance column, distinct from an estimate that was legitimately `$0`.

## Testing

Following the existing pytest + in-memory-SQLite convention (`tests/conftest.py` fixtures: `app`, `client`, `make_tenant`):

- **Model test:** `MonthlyProfitEstimate` defaults and `to_dict()`.
- **Computation test:** `recalculate_estimated_profit(tenant_id)` against a mix of customers — plan-default cost, per-customer `cost_override`, monthly vs. yearly billing cycles (amortization), and an inactive customer (excluded) — asserting exact `estimated_income`/`estimated_cost`/`estimated_profit` sums.
- **Trigger tests:** adding a customer, editing `discount`/`cost_override`, editing a plan's `price`/`cost`, deactivating/reactivating/deleting a customer, switching a customer's plan — each asserted to update the current month's row, and to leave a manually-seeded *past* month's row untouched (proving the freeze-by-time-passing behavior).
- **Scheduler test:** mirrors `tests/test_iso_scheduler_payroll.py` — calls `recalculate_all_estimated_profits_with_context`'s underlying per-tenant function directly with an explicit `tenant_id` and no request/JWT context, asserts tenant isolation (no cross-tenant rows, no `tenant_id IS NULL` rows).
- **API test:** `GET /api/reports/financial` includes `estimated_profit`/`variance` per month, lazily backfills the current month when missing, and returns `null` (not `0`) for past months with no logged row.

## Global Constraints (carried into the implementation plan)

- Existing tenant data is never altered by this feature — all schema changes are additive (two new nullable/defaulted columns, one new table); no backfill of historical `MonthlyProfitEstimate` rows for months before this feature ships (those months legitimately have no estimate, and the UI shows `—` for them).
- `MonthlyProfitEstimate` must be tenant-scoped exactly like every other table (`tenant_id` FK + `TENANT_OWNED_MODELS` registration).
- `recalculate_estimated_profit(tenant_id)` never calls `tenant_query(...)` — explicit `tenant_id` filtering only, since it must run from the scheduler with no request context.
- No proration; no changes to existing cash-basis `income`/`expenses`/`profit` calculations — this feature only adds parallel `estimated_profit`/`variance` fields alongside them.
