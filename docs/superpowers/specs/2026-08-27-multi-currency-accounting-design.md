# Multi-Currency Accounting for Tenant Customer Billing — Design

**Status (2026-08-27): design only, nothing built yet.** Phase 4b of the post-audit roadmap. Bundles two previously-separate findings, both schema changes to the same money columns: (1) full multi-currency support for a tenant's *own customer billing* (not the ServiceBills-Pro-subscription billing covered by the 2026-08-26 Whish spec — that stays USD-only, untouched), and (2) the Float→Numeric migration for money columns, noted as a separate finding but folded in here since doing both at once avoids two schema migrations on the same columns.

## Background & requirements already decided (not re-litigated here)

From a prior conversation with the business owner:
- Full multi-currency: a currency field on relevant money-holding records, an FX-rate table, and **historical rate-locking** — each transaction/payment freezes the FX rate in effect when it occurred; that rate never floats retroactively when the tenant enters a new rate later. This is real conversion/storage semantics, not a display-label change.
- **Per-tenant opt-in**, default OFF, mirroring `BusinessSettings.upstream_sync_automation_enabled`: a tenant who never turns this on sees no new fields, no new UI, no behavior change at all.
- Bundle the Float→Numeric migration for money columns into the same migration.

## Correction to the Float→Numeric column list

The task brief listed `Collector.balance` as a column to convert. **No `Collector` model exists in `app.py`.** Grep confirms it: the closest concepts are `Payment.collected_amount`, `Payment.collected`/`collected_by_id` (a payment collected by a *User* acting as a collector — there is no separate `Collector` entity or balance). Treating the brief's list as a starting point and verifying against the real models (`grep -n "db.Column(db.Float" app.py`), the complete, verified list of `db.Float` money columns in `app.py` as of this spec is:

| Model | Columns |
|---|---|
| `Reseller` | `balance` |
| `ResellerPayment` | `amount` |
| `UpstreamProvider` | `balance` |
| `UpstreamProviderPayment` | `amount` |
| `Customer` | `balance`, `discount`, `cost_override` |
| `SubscriptionPlan` | `price`, `cost` |
| `Supplier` | `balance` |
| `SupplierPayment` | `amount` |
| `Expense` | `amount` |
| `Employee` | `monthly_salary`, `balance` |
| `SalaryCharge` | `amount` |
| `SalaryPayment` | `amount` |
| `MonthlyProfitEstimate` | `estimated_income`, `estimated_cost`, `estimated_profit` |
| `Payment` | `amount`, `collected_amount` |
| `AddonPurchase` | `amount` |
| `BillingPaymentAttempt` | `amount` |

23 columns across 12 models. `BillingPaymentAttempt.amount` is USD-only platform-subscription money (2026-08-26 Whish spec) — it is converted to `Numeric` for consistency (no more `Float` money columns anywhere in the schema) but gets **no currency/FX treatment**; it stays implicitly USD, matching that spec's explicit non-goal ("No multi-currency accounting integration").

## Explicit non-goals

- **Not touching platform-subscription billing.** `Tenant.plan`, `BillingPaymentAttempt`, Whish self-serve Pro billing (2026-08-26 spec) stay USD-only. This spec is about a *tenant's own customers* paying *that tenant* in USD/LBP/etc — a different money flow entirely.
- **No automatic external FX-rate API integration.** Manual rate entry by tenant admin staff, per the recommended default (see FX rate source below). If a future spec adds an external source, it must degrade gracefully to manual entry — this spec doesn't build that integration, only leaves room for it (the `ExchangeRate.source` column below).
- **No historical backfill/re-denomination of existing money.** Every existing row (`Payment`, `Customer.balance`, etc.) implicitly represents the tenant's single existing currency today. The migration does not retroactively assign or convert anything — see Rollout & data safety.
- **No frontend UI in this PR.** This spec and its implementation plan are backend-only: the data model, migration, FX rate CRUD API, currency-aware payment/reporting logic, and tests. Building the React UI (currency picker on Customer/SubscriptionPlan forms, an FX-rate-entry screen, reporting-currency selector in Settings) is real, non-trivial frontend work that the task brief's "small TDD tasks" framing doesn't fit well, and this repo has no frontend test suite to hold it to the same rigor as the backend. **This is a deliberate scope decision, not an oversight** — flagged prominently in the PR description as follow-up work needed before this feature is usable end-to-end by a tenant. The backend is fully opt-in and additive, so shipping it with no UI yet changes nothing for any tenant.
- **No multi-currency support for Reseller/UpstreamProvider/Supplier/Employee ledgers.** Those columns are converted to `Numeric` (bundled Float→Numeric fix) but do **not** get a `currency` column or FX-lock in this spec. Rationale: the business owner's requirements frame this as "tenants' own customer billing" — `Customer`/`SubscriptionPlan`/`Payment`. Reseller/upstream-provider/supplier money is the tenant's *own* cost-side ledger with its own upstream counterparty, almost always in one currency by construction (a given Reseller or UpstreamProvider relationship is agreed in one currency), and extending multi-currency there roughly doubles this spec's surface (balance-carrying ledgers instead of point-in-time transactions — locking a rate for a running *balance* rather than a single payment is a materially different, harder problem: does the balance itself get redenominated when the rate changes, or only new postings?). Flagged as an open question for the business owner below, not decided here.
- **No changes to how `Payment.collected_amount`, refunds, or gratis payments compute their amounts.** They inherit `Payment.currency`/`fx_rate_to_reporting` from the parent payment row unchanged; no new conversion logic for those paths.
- **FX locking is wired into exactly one `Payment`-creation code path in this PR: the explicit manual endpoint (`POST /api/payments` / `add_payment`).** A `grep -n "Payment(" app.py` audit performed during implementation found roughly a dozen other places that construct a `Payment` row directly: the back-dated payment backfill inside `add_customer`, the daily `generate_missing_payments`/`generate_missing_payments_with_context` scheduler job, the manual "generate missing payments" button, subscription renewal/reactivation, partial-payment remainder splits, and reseller-to-independent debt reassignment. These are **deliberately left unwired** in this PR — a judgment call, not an oversight, made for a concrete reason: several of these (most importantly `generate_missing_payments`) wrap an entire per-tenant, multi-customer loop in one top-level `try/except` with a single `db.session.rollback()` — making `fx.get_rate()` raise inside that loop (the correct behavior per "a missing rate must block, never silently mis-convert") would silently abort *that whole tenant's* backdated-billing run over one customer's missing FX rate, a severe regression to a batch revenue-recognition path this spec's author judged too risky to restructure and re-verify with confidence in this pass. Every one of these unwired sites continues to construct `Payment` rows with the model's plain defaults (`currency='USD'`, `fx_rate_to_reporting=1`) — **exactly correct, byte-identical to pre-this-PR behavior, for every opted-out tenant** (the only behavior-preservation guarantee this task actually requires), but a **known, real gap for an opted-in multi-currency tenant**: an auto-generated recurring charge on a non-USD plan will be mislabeled as USD/rate-1 until a follow-up PR extends the same locking helper to these paths (ideally via a shared, tested helper function, with each batch loop restructured to catch a per-customer `FxRateMissingError` and skip-with-log rather than abort the whole tenant, matching this codebase's existing per-customer exception-isolation pattern in `auto_sync_upstream_status_for_tenant`). This is called out again in the PR description, not left implicit.

## Currency model

A `Currency` reference table, not a hardcoded two-value enum, per the explicit design instruction to allow adding currencies later without a schema change:

```python
class Currency(db.Model):
    """Reference table of currencies this deployment knows about. Seeded with
    USD/LBP (the two that matter for this market) but adding a third currency
    is a data insert, not a migration -- see the multi-currency accounting
    design spec."""
    code = db.Column(db.String(3), primary_key=True)  # ISO 4217, e.g. 'USD', 'LBP'
    name = db.Column(db.String(50), nullable=False)
    decimal_places = db.Column(db.Integer, nullable=False, default=2)
    active = db.Column(db.Boolean, nullable=False, default=True)
```

Not tenant-scoped — this is a small, platform-wide reference list (like `plans.PLANS`), seeded by the migration with:
- `USD`, "US Dollar", `decimal_places=2`
- `LBP`, "Lebanese Pound", `decimal_places=0` (LBP has no minor unit in practical use at current valuations; see Precision below)

`decimal_places` exists so the (future) frontend can format amounts correctly per currency; nothing in this backend-only PR reads it yet except a validation helper described below.

## Data model

### `BusinessSettings` gains two columns (the opt-in + reporting currency)

```python
multi_currency_enabled = db.Column(db.Boolean, nullable=False, default=False)
reporting_currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False, default='USD')
```

`multi_currency_enabled` is the opt-in flag, directly mirroring `upstream_sync_automation_enabled`'s precedent: off by default, a per-tenant boolean, gating an entire feature area. `reporting_currency` exists **regardless** of the flag (defaults to `'USD'` for every tenant, single-currency or not) because it doubles as "the tenant's one currency" for a single-currency tenant — this means reporting/aggregation code has exactly one code path (always convert-and-sum into `reporting_currency`) rather than a flag-gated branch, which is both simpler and safer (see Reporting below). For an opted-out tenant, every payment is created in `reporting_currency` and every FX rate is implicitly 1:1, so "convert" is a no-op — opted-out tenants pay zero performance or correctness cost for code that always converts.

### `SubscriptionPlan` gains one column

```python
currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False, default='USD')
```

### `Customer` gains **no** new currency column

Decision: currency lives on `SubscriptionPlan`, not `Customer`. Rationale, reasoning from how `billing_cycle`/`price` already work in this codebase: a `Customer` doesn't carry its own price — it references a `SubscriptionPlan` and inherits `price`/`cost`/`billing_cycle` from it (see `add_customer`'s `subscription_plan.price - discount` and `SubscriptionPlan.to_dict()`). Currency is exactly the same kind of plan-level attribute as price: a tenant creates a "Fiber 100Mbps — LBP" plan alongside a "Fiber 100Mbps — USD" plan if it wants to sell the same service in two currencies to different customer segments, the same way it already creates separate plans for different price points. This avoids a currency mismatch ever being possible between a customer's plan price and what a payment charges — the currency simply *is* whatever `subscription_plan.currency` says, with no separate customer-level override to keep in sync. If a customer needs to switch currency, that's already exactly how a plan change works today (assign them to a different plan) — no new mechanism needed.

`Customer.balance`/`discount`/`cost_override` are denominated in the customer's current `subscription_plan.currency`. **This has a real edge case**: if a customer's plan is changed to a plan in a *different* currency, their outstanding `balance` (money value, not row count) does not auto-convert — it's simply now interpreted in the new plan's currency, silently changing its real-world value. This is called out explicitly as a known gap in Non-goals-adjacent risk below; the mitigation in this PR is a validation guard (see Task list) that blocks a plan change across currencies for a customer with a non-zero `balance`, forcing a manual zero-out/settle-first path rather than silently mis-valuing money. A cleaner UX (an explicit "convert this customer's balance to the new currency at rate X" flow) is real product work, listed as an open question below.

### `Payment` gains two columns — this is where rate-locking happens

```python
currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False, default='USD')
# The rate used to convert `amount` (in `currency`) into the tenant's
# reporting_currency AT THE TIME this payment was created. Frozen forever --
# never recomputed when new ExchangeRate rows are added later. 1.0 when
# currency == reporting_currency (no conversion needed, exact by construction,
# not merely by convention -- see fx.py). This is the actual "historical
# rate-locking" mechanism the spec requires.
fx_rate_to_reporting = db.Column(db.Numeric(18, 8), nullable=False, default=1)
```

Why lock the rate on `Payment` specifically (not `Customer`, not a new join table): `Payment` is this codebase's one true point-in-time money-movement record — every dollar (or LBP) that changes hands is a `Payment` row, and it already has an immutable `date`/`paid_at`. Locking the rate here, once, at creation, and never touching it again is the simplest correct implementation of "freezes the exchange rate in effect when it occurred." No other model needs its own lock: `Customer.balance` is a running total *in one currency* (the plan's currency) and is never itself converted; reports convert on read using each `Payment`'s own locked rate (see Reporting).

`AddonPurchase.amount` is **not** given its own `currency`/`fx_rate_to_reporting` pair. Rationale: every `AddonPurchase` is tied to exactly one `customer_id` and (via `payment_id`, once paid) to the `Payment` that settles it — it inherits the customer's plan currency and, once paid, the settling payment's locked rate. Duplicating rate-lock columns onto a row that's fundamentally "a pending or paid charge against a customer" (the same relationship `Payment` already has) would be redundant state that could drift from the truth on `Payment`. This is a judgment call, listed below for review.

### `ExchangeRate` — the FX-rate table, manually populated

```python
class ExchangeRate(db.Model):
    """A tenant-entered FX rate, effective from a point in time until superseded.
    Historical Payment rows never re-read this table after creation (they store
    their own locked fx_rate_to_reporting) -- this table is consulted only (a) at
    payment-creation time, to pick the rate to lock in, and (b) for
    reports/conversions of NON-Payment current-state figures (e.g. "what is this
    customer's LBP balance worth in USD right now") that intentionally want the
    latest rate, not a frozen one."""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    from_currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False)
    to_currency = db.Column(db.String(3), db.ForeignKey('currency.code'), nullable=False)
    # 1 unit of from_currency = `rate` units of to_currency.
    rate = db.Column(db.Numeric(18, 8), nullable=False)
    effective_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # 'manual' today; reserved for a future external-API source, per the design
    # instruction that any such integration must degrade gracefully to manual
    # entry -- this column is what would let both kinds of rows coexist.
    source = db.Column(db.String(20), nullable=False, default='manual')
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_exchange_rate_tenant_pair_effective', 'tenant_id', 'from_currency', 'to_currency', 'effective_at'),
    )
```

Rate lookup for a given `(tenant, from, to, as_of)` (used at payment-creation time to populate `fx_rate_to_reporting`): the latest `ExchangeRate` row for that tenant/pair with `effective_at <= as_of` (defaulting `as_of = now()` for a payment being created right now), i.e. "the rate in effect at that moment," not "the rate entered most recently by wall-clock." This lets a tenant backfill/correct a rate for a specific past date without disturbing what "current" means, and matches how `Payment.date`-based reporting already treats a payment's own `date` as its authoritative point in time elsewhere in this codebase.

If `from_currency == to_currency`, the rate is always exactly `1` and no `ExchangeRate` row is needed or consulted — enforced in code (`fx.py`), not just by convention, so a single-currency (opted-out) tenant never needs an `ExchangeRate` row at all.

If no applicable `ExchangeRate` row exists for a cross-currency pair, `fx.get_rate()` raises (`FxRateMissingError`) rather than silently defaulting to `1` or guessing — a missing rate must block the payment from being created wrongly-valued, not silently mis-convert it (see FX rate entry/lookup/locking semantics below for exactly where this surfaces to the API caller).

## FX rate source

**Recommended default: manual entry, no external API**, per the instruction to favor this codebase's established conservative stance on external dependencies (WhatsApp/upstream-portal integrations, `email_util.py`'s SMTP→SendGrid→console fallback chain). A tenant admin enters `ExchangeRate` rows by hand (e.g. "as of this morning, 1 USD = 89,500 LBP") via a new small CRUD API (`POST /api/exchange-rates`, `GET /api/exchange-rates`) gated by `multi_currency_enabled`.

No external FX API is integrated in this PR. Reasoning beyond the stated preference: LBP's real-world exchange rate is set by informal/parallel-market dynamics that free-tier FX APIs (built for official/interbank rates) generally don't track accurately for this market — manual entry by someone who actually knows the current street rate is *more* reliable here, not a fallback compromise. If a future spec adds an external source, `ExchangeRate.source` already provides the seam (rows tagged `'api'` vs `'manual'`) and the graceful-degradation requirement means: if the API is unavailable, checkout/payment-creation falls back to "use the latest existing `ExchangeRate` row of any source" rather than blocking, exactly like `email_util.py` never blocks on a mail-backend failure.

## Precision & the Float→Numeric migration

All 23 columns listed above convert `Float` → `Numeric(18, 4)`.

**Why `Numeric(18, 4)` specifically:**
- **Scale (4 decimal places)**: covers USD cents (2dp) with headroom, and covers `fx_rate_to_reporting`-driven fractional cents from conversion without a second rounding step before storage (a converted amount like `120.00 USD * 89,542.37 LBP/USD` needs more than 2dp of intermediate precision to round correctly once, rather than round-then-round-again). Not used for `ExchangeRate.rate`/`fx_rate_to_reporting` themselves, which get their own `Numeric(18, 8)` (see below) — 4dp would be far too coarse for a *rate* (an 8dp rate on a ~89,500 LBP/USD pair still carries ~4 significant decimal digits of real precision; 4dp on the rate itself would silently round away real rate precision on every lock).
- **Precision (18 total digits)**: the task brief calls out that LBP nominal amounts run into the hundreds of thousands to millions for ordinary transaction sizes given historical depreciation. 18 digits gives 14 digits left of the decimal point at scale 4 — comfortably covers LBP amounts many orders of magnitude beyond any realistic single transaction or running balance (a customer balance of even 999,999,999,999.9999 LBP is absurdly larger than anything this business could plausibly reach), while matching this codebase's general preference for generous, unlikely-to-need-revisiting bounds over precisely-fitted ones.
- Verification of real magnitudes: this spec's author does not have production data access. The implementation plan's migration-verification task explicitly includes querying representative existing values (`SELECT MAX(price), MIN(price) FROM subscription_plan`, etc. — see Testing/Migration plan) against the docker-compose Postgres dry-run's *schema* to confirm `Numeric(18,4)` isn't somehow already too narrow for what's on disk today (it won't be — existing data is `Float`, i.e. already bounded by IEEE-754 double range, which `Numeric(18,4)` comfortably covers for any value that was ever meaningfully entered as a price/amount in this app's UI) — this is a sanity check, not expected to find a problem.

`ExchangeRate.rate` and `Payment.fx_rate_to_reporting`: `Numeric(18, 8)`. 8 decimal places of rate precision comfortably represents both a USD→LBP rate (large magnitude, e.g. `89542.37000000`) and an LBP→USD rate (small magnitude, e.g. `0.00001117`) without losing significant digits in either direction.

**Implementation-time decision: `asdecimal=False` on all 23 money-amount columns, `asdecimal=True` (the default) on the two rate columns.** SQLAlchemy's `Numeric` type defaults to returning Python `Decimal` on read; a `grep -n "\.balance +=\|\.balance -=" app.py` and equivalent scan during implementation found this 7700+ line file's billing logic is pervasively written as plain Python `float` arithmetic against these columns (`reseller.balance += amount_due`, `customer.balance -= payment_amount`, `subscription_plan.price - discount`, and dozens more) — `Decimal + float` raises `TypeError` in Python, so switching these 23 columns to real `Decimal` without `asdecimal=False` would require auditing and rewriting every such call site, a much larger and riskier change than this PR's actual goal. `Numeric(18, 4, asdecimal=False)` still gets the real fix (exact fixed-point storage and Postgres-side precision, closing the float-imprecision risk that motivated the bundled Float→Numeric finding in the first place) while returning ordinary Python `float` to every existing call site, unchanged. The two FX-rate columns are deliberately **not** given `asdecimal=False`: `fx.get_rate()` and the rate-locking code explicitly work in `Decimal` (`Decimal(direct.rate)`, `Decimal('1') / Decimal(inverse.rate)`), and converting a rate to `Decimal` from an already-lossy `float` would bake in binary floating-point representation error exactly where this feature's precision matters most — the two rate columns are new, narrowly-scoped, and have no pre-existing float-arithmetic call sites to break.

**Postgres behavior for `Float`→`Numeric`**: a direct `ALTER COLUMN ... TYPE NUMERIC(18,4) USING column::numeric(18,4)` is safe on Postgres for existing data — every value that was ever stored as an IEEE-754 double from a UI-entered price/amount converts cleanly (no existing value has enough fractional garbage from float imprecision to matter at 4dp; if it did, this migration is exactly what fixes that, which is the other half of why this bundled fix matters). This is asserted here and **must be confirmed for real** against the docker-compose Postgres dry-run before considering the migration done (see Migration plan) — not merely assumed from this reasoning.

## FX rate entry/lookup/locking semantics

**Entry**: `POST /api/exchange-rates` (JWT + admin, tenant-scoped, `400` if `multi_currency_enabled` is false — no point letting a tenant populate a table their own payment flow will never consult). Body: `{"from_currency": "USD", "to_currency": "LBP", "rate": 89542.37, "effective_at": "2026-08-27T09:00:00"}` (effective_at optional, defaults to now). Validates both currency codes exist in `Currency` and are `active`, `rate > 0`.

**Lookup** (`fx.get_rate(tenant_id, from_code, to_code, as_of=None)`, in a new `fx.py` module):
1. `from_code == to_code` → return `Decimal('1')`, no DB query.
2. Otherwise, query the latest `ExchangeRate` row for `(tenant_id, from_code, to_code)` with `effective_at <= as_of` (default `as_of=datetime.utcnow()`), ordered by `effective_at desc`.
3. If none found, try the **inverse** pair `(to_code, from_code)` the same way and return `1 / rate` — a tenant only has to enter "1 USD = X LBP," not both directions. (If somehow both directions exist for overlapping periods, the direct direction wins — simpler than reconciling two independently-entered rates.)
4. If neither exists, raise `FxRateMissingError`.

**Locking** (at `Payment`-creation time, in `add_payment` and any other payment-creating code path — see the implementation plan for the full list): `fx_rate_to_reporting = fx.get_rate(tenant_id, payment.currency, business_settings.reporting_currency, as_of=payment.date)`. If this raises `FxRateMissingError`, the payment is **not created** — the API returns `400` with a clear message ("No exchange rate on file for USD→LBP as of this payment's date; enter one under Settings → Exchange Rates first."), matching this codebase's existing validation-before-side-effect pattern (`_parse_positive_amount` etc. already reject before any `db.session.add`).

For a single-currency (opted-out) tenant, `payment.currency` is always `reporting_currency` (no other choice is exposed), so `fx.get_rate` always hits case 1 above (`from_code == to_code`) and the rate is always exactly `1` — this is what makes the opt-in genuinely free of behavior change for an opted-out tenant, not merely free of new UI.

## Reporting-currency conversion

**Decision: yes, `reporting_currency`** (already introduced above), used by every report that sums money across `Payment` rows. This directly answers the open design question: "do reports need a reporting-currency concept" — yes, because a multi-currency tenant's `ReportsView`/`EnhancedReportsView` totals (`/api/reports/financial`, `/api/reports/total-sales`, `/api/reports/revenue`, etc.) currently do a bare `SUM(Payment.amount)`, which is meaningless the moment two payments in the same sum are in different currencies (summing raw USD and raw LBP figures produces a number that is not a real amount of anything).

**Mechanism**: every such `SUM(Payment.amount)` becomes `SUM(Payment.amount * Payment.fx_rate_to_reporting)`. This is exactly why the rate is locked *per payment* rather than computed live: a report over a date range spanning a rate change must use each payment's own historical rate, not one blended "current" rate, or the reported total for last month would silently change every time the tenant updates this month's rate. Since `fx_rate_to_reporting` is `1` for every payment when the tenant is opted out (or for any payment already in `reporting_currency`), this SQL expression is a no-op multiply-by-1 for single-currency tenants — no behavior change, verified by a test that asserts opted-out-tenant report totals are byte-identical to today's (mathematically: `SUM(amount * 1) == SUM(amount)`).

**Labeling**: every report response gains a `"currency": "<reporting_currency>"` field alongside its totals, so the frontend (a later PR) can render "Total: 1,234.56 USD (converted)" rather than a bare unlabeled number — flagged as a required frontend follow-up, not built here.

**Scope of this PR's reporting change**: only the aggregate `SUM`/`func.sum(Payment.amount)` call sites are updated to the `* fx_rate_to_reporting` form (a mechanical, per-callsite change enumerated in the implementation plan). Line-item/detail views (e.g. a single payment in a list) continue to show `Payment.amount` in `Payment.currency` as-is, with the currency code alongside it — converting a single already-correct amount for display would be a lossy round-trip for no benefit; conversion is a reporting/aggregation concern only, not a "recolor every number in the app" concern.

## Migration plan

One Alembic migration (or, if ordering makes the diff clearer, two migrations landing together in the same PR — decided during implementation, not fixed here), following this repo's defensive existence-check pattern throughout (`inspect(bind)` before every `ADD`/`CREATE`, `NOTE:`-and-skip rather than crash if already present, per `c57bc44a51d0`'s documented rationale):

1. `CREATE TABLE currency` (if not exists) + seed `USD`/`LBP` rows (idempotent upsert-by-primary-key, not a blind `INSERT`, so a second run of this migration on a DB that already has the seed rows doesn't crash on the PK conflict).
2. `CREATE TABLE exchange_rate` (if not exists), with its FKs and the composite index.
3. `ALTER TABLE business_settings ADD COLUMN multi_currency_enabled` (nullable=False, server_default `false`) and `ADD COLUMN reporting_currency` (nullable=False, server_default `'USD'`, FK to `currency.code`) — both skip-if-exists.
4. `ALTER TABLE subscription_plan ADD COLUMN currency` (nullable=False, server_default `'USD'`, FK to `currency.code`) — skip-if-exists.
5. `ALTER TABLE payment ADD COLUMN currency` (nullable=False, server_default `'USD'`, FK to `currency.code`) and `ADD COLUMN fx_rate_to_reporting` (`Numeric(18,8)`, nullable=False, server_default `1`) — skip-if-exists.
6. The 23-column Float→Numeric conversion, each column individually existence/type-checked (skip if a column is already `Numeric`/`DECIMAL` — relevant if this migration is ever re-run partially) before altering. On Postgres: `op.alter_column(table, col, type_=sa.Numeric(18, 4), postgresql_using=f'{col}::numeric(18,4)')`. On SQLite (dev, via `batch_alter_table`, `render_as_batch=True` already configured in `app.py`): SQLite has no real `NUMERIC` type enforcement (it's dynamically typed and stores whatever Python/SQLAlchemy hands it), so this is a metadata-only change there — dev/test behavior is unaffected, which is exactly why the Postgres dry-run in the next section is non-negotiable, not a formality.
   - `ExchangeRate.rate`/`Payment.fx_rate_to_reporting` are created directly as `Numeric(18,8)` in step 2/5 above, not part of this 23-column list (they're new columns, not Float→Numeric conversions).

All server_defaults exist so the `ADD COLUMN ... NOT NULL` steps succeed against a populated production table in one pass (Postgres requires either a default or a nullable column when adding `NOT NULL` to a non-empty table) — this is the same reasoning already documented in this repo's other migrations that add `NOT NULL` columns to existing rows.

**Downgrade**: drops the new columns/tables and reverts `Numeric` back to `Float` (data loss of sub-cent precision on downgrade is accepted and noted in the migration's docstring — a downgrade path exists for emergency rollback, not as a precision-preserving round-trip).

### Verifying against real Postgres (non-optional, per this repo's documented history)

Run before considering the migration done:
```bash
docker compose up -d db
# wait for healthy
DATABASE_URL=postgresql+psycopg2://servicesbills:localdevpass@localhost:5432/servicesbills \
  JWT_SECRET_KEY=test SECRET_KEY=test flask db upgrade
```
Then, against that same Postgres instance, confirm: `\d payment`, `\d subscription_plan`, `\d business_settings`, `\d exchange_rate`, `\d currency` show the expected column types/constraints; a manual `INSERT`/`SELECT` round-trip on a few rows confirms `Numeric` values survive intact; `flask db downgrade -1` (repeated back through this migration) and `flask db upgrade` again both succeed cleanly (verifies the downgrade path isn't merely decorative).

## Security & tenancy

- `Currency` is the one new table that is **not** tenant-scoped (a shared reference list, like `plans.PLANS`) — not added to `TENANT_OWNED_MODELS`, has no `tenant_id`, and every route touching it is read-only for tenants (no tenant can create/modify currencies in this PR).
- `ExchangeRate` **is** tenant-scoped (`tenant_id`, added to `TENANT_OWNED_MODELS`, created via `new_for_tenant`, read via `tenant_query`) — one tenant's LBP rate is never visible to or usable by another tenant, consistent with every other per-tenant financial table in this app.
- No new external network calls, no new secrets to store (this spec deliberately has no external FX API), so no new attack surface beyond a standard tenant-scoped CRUD endpoint pair.

## Testing approach

New `tests/test_multi_currency.py`, following this repo's `make_tenant`/`auth_headers` fixture conventions (`tests/conftest.py`), covering:
- `Currency`/`ExchangeRate` model roundtrips (mirrors `test_billing_payment_attempt_model_roundtrip`'s pattern).
- `fx.get_rate()`: same-currency short-circuit (no DB query, returns exactly `1`), direct-pair lookup, inverse-pair fallback, "as of" date semantics (a later rate doesn't affect a lookup `as_of` an earlier date), `FxRateMissingError` when nothing applies.
- `POST /api/exchange-rates`: rejects when `multi_currency_enabled` is false; rejects unknown/inactive currency codes; rejects non-positive rate; tenant-isolation (tenant A's rate never visible to tenant B — following this repo's `test_iso_*.py` isolation-test convention).
- `add_payment` (or wherever payment creation is centralized): opted-out tenant behavior is **byte-identical** to pre-this-PR behavior (currency always `reporting_currency`, `fx_rate_to_reporting` always `1`, no `ExchangeRate` needed); opted-in tenant with a currency-mismatched payment and no applicable rate gets `400`, payment not created, customer balance unchanged (transactional rollback verified); opted-in tenant with a rate on file gets a payment with the correct locked `fx_rate_to_reporting`, and entering a *new* rate afterward does not change the already-created payment's locked rate (the actual regression test for "historical rate-locking," not just a unit test of `fx.get_rate` in isolation).
- Reports (`/api/reports/financial` at minimum, representative of the `SUM(Payment.amount)` call sites): opted-out tenant totals unchanged from current behavior; opted-in tenant with mixed-currency payments reports the correctly-converted total and the `"currency"` field.
- Cross-currency plan-change guard: reassigning a customer with non-zero `balance` to a `SubscriptionPlan` in a different currency is rejected with a clear error (see Customer section above).
- Float→Numeric: a targeted test (SQLite, since Postgres is verified separately per Migration plan) that existing money fields still round-trip correctly as Python `Decimal`/`float` through the ORM after the type change — this repo's `to_dict()` methods already wrap money fields in `float(...)`, so this test also confirms none of those `float()` calls break on a `Decimal` input (they don't — `float(Decimal(...))` is well-defined — but this is asserted, not assumed).
- This repo's existing full suite (`python -m pytest -q`) must stay green throughout — many existing tests construct `Payment`/`SubscriptionPlan`/etc. rows and will exercise the new `currency`/`fx_rate_to_reporting` defaults incidentally; where a default (`'USD'`, `1`) doesn't satisfy an existing assertion, the fix is in the new column's default, not in loosening the existing test.

No scheduler involvement in this spec (FX rates are entered manually, not refreshed on a timer) — the scheduler crash-loop lesson from 2026-08-26 does not apply here; this is called out explicitly so no reviewer has to double back and check.

## Rollout & data safety

- **Migration is additive-only for every existing tenant's behavior**: new nullable-with-default columns and new tables; every existing `Payment`/`SubscriptionPlan`/`BusinessSettings` row gets `currency='USD'`/`fx_rate_to_reporting=1`/`reporting_currency='USD'`/`multi_currency_enabled=false` by server_default, which is a correct, truthful statement about that tenant's existing data (this app has only ever supported one implicit currency; calling it `'USD'` by default is a labeling choice — flagged as an open question below in case any existing tenant's implicit currency is actually LBP, which would make `'USD'` a wrong default for them specifically).
- **The Float→Numeric conversion is the one part of this migration that touches every existing row's storage representation**, not just adds columns — this is exactly why the Postgres dry-run (not merely a SQLite test run) is mandatory before merge, per this repo's documented history of migrations that passed on SQLite and broke on Postgres.
- **No tenant sees any new UI or behavior change until (a) this backend ships, (b) a future frontend PR adds the currency-picker/FX-entry UI, and (c) that tenant explicitly flips `multi_currency_enabled` on.** Three gates, not one — even after this backend PR merges and deploys, nothing changes for anyone until the (separate, future) frontend work ships too.
- First real multi-currency tenant onboarding (once the frontend exists) should be a supervised manual walkthrough with that tenant's actual admin, same discipline as this repo's other high-stakes rollouts (Krypton adapter, Whish billing) — not decided further here, since it's operationally downstream of the not-yet-built frontend.

## Open questions for the business owner

These are genuine product/business calls, not engineering decisions — surfaced rather than guessed:

1. **Is any existing tenant's *actual* implicit currency LBP, not USD?** This migration defaults every existing tenant's historical data to `currency='USD'`. If some existing tenant has actually been billing in LBP all along (just without a currency field to say so), this default mislabels their history. Needs a real answer from whoever knows this app's existing tenant roster, not a guess.
2. **Should Reseller/UpstreamProvider/Supplier/Employee balances eventually get multi-currency too**, or are those correctly scoped out as single-currency-by-construction (per Non-goals above)? If yes, that's a follow-up spec — the balance-vs-transaction rate-locking problem it raises (see Non-goals) needs its own design pass.
3. **What should happen to a customer's outstanding balance when their subscription plan changes to a different currency?** This spec blocks the change outright when balance ≠ 0 (safest, but may be too restrictive operationally — a tenant genuinely needing to migrate a customer between currency-denominated plans has no path forward except manually settling to zero first). An explicit "convert at rate X, log it as an adjustment" flow is real product design work.
4. **Should the frontend currency-picker UI (the deliberately-deferred half of this feature) be prioritized right after this backend PR, or is backend-only useful as a standalone increment** (e.g. so a developer/support person can manually configure FX rates and currency-tagged plans via direct API calls or a future admin tool, ahead of self-serve tenant UI)? Affects how urgently the follow-up frontend PR should be scheduled.
5. **How urgently should the remaining `Payment`-creation code paths (recurring/backdated billing, renewals, partial-payment splits — see Non-goals above) get FX-locking wired in?** Until that follow-up lands, a multi-currency tenant's auto-generated charges are silently mislabeled as USD/rate-1, which is a real (if narrow, since it only bites tenants who've actually opted in) financial-accuracy gap. This spec's author judged the batch-loop restructuring needed to close it safely as too large/risky to do in the same pass as everything else here — worth an explicit call on how soon that follow-up should happen, given it directly affects the correctness of numbers a tenant sees.
