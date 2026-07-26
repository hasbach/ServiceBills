# Estimated vs. Real Monthly Profit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Model a per-subscription cost (plan default + optional per-customer override), compute an amortized monthly estimated profit from currently active subscriptions, log it per tenant per month (frozen once the calendar rolls over), and show it alongside the existing cash-basis real profit on the Financial Report with a variance column.

**Architecture:** Two new nullable/defaulted columns (`SubscriptionPlan.cost`, `Customer.cost_override`) plus one new table (`MonthlyProfitEstimate`, one row per tenant per month). A single function, `recalculate_estimated_profit(tenant_id)`, recomputes the *current* month's row from currently-active customers; it's called from every customer/plan lifecycle endpoint that could change the number (add/edit/cancel/reactivate/delete a customer, edit a plan's price/cost) and from a new daily scheduler job as a safety net for quiet months. `GET /api/reports/financial` is extended in place to join in the logged estimate and a computed variance.

**Tech Stack:** Flask + Flask-SQLAlchemy + Flask-Migrate (Alembic, `render_as_batch=True`), APScheduler `BackgroundScheduler` (already running two jobs), pytest (in-memory SQLite via `tests/conftest.py`), React + MUI + Recharts frontend.

## Global Constraints

- **Existing tenant data is never altered.** All schema changes are additive: `SubscriptionPlan.cost` and `Customer.cost_override` are new nullable/defaulted columns added to existing tables (a `server_default` backfills existing rows automatically — this only ever *fills in* the new column, never touches any other field), and `MonthlyProfitEstimate` is a brand-new table. No historical backfill of estimates for months before this feature ships — those months legitimately have no estimate and the UI shows `—`.
- **Amortization:** `effective_price = max(0, plan.price - customer.discount)`; `effective_cost = customer.cost_override if not None else plan.cost`. Monthly plans contribute the full `(effective_price - effective_cost)`; yearly plans contribute `(effective_price - effective_cost) / 12`. A customer on a plan with any other `billing_cycle` value is skipped (contributes `0`), matching `generate_missing_payments`'s existing skip-on-unrecognized-cycle behavior. No proration for mid-month signups/cancellations.
- **`recalculate_estimated_profit(tenant_id)` never calls `tenant_query(...)`** — it takes an explicit `tenant_id` and filters every query by it directly, exactly like `generate_missing_payments`/`generate_missing_salary_charges`, since it must also run from the scheduler with no request/JWT context. It never raises — internally `try/except: db.session.rollback(); print(...)`, matching those same functions, so a glitch here can never block the primary action (creating a customer, etc.) that triggered it.
- **One row per tenant per month, upserted only for the current month.** `MonthlyProfitEstimate` has a `unique(tenant_id, month)` constraint. Once the calendar rolls into a new month, nothing ever targets last month's row again — that's what makes it a frozen historical record, with no explicit "close the month" step needed.
- **Ledger reads are additive on the Financial Report** — `GET /api/reports/financial`'s existing `income`/`expenses`/`profit` fields and calculation are untouched; `estimated_profit` and `variance` are new parallel fields, `null` (not `0`) for any month with no logged estimate.
- **All file paths absolute** under `C:\Users\InfoCenter\source\repos\delta-net-saas\`.
- **Tests first, in-memory SQLite.** New backend tests go in `tests/test_profit_estimation.py` (created in Task 1, appended to in later tasks), using the existing `app`/`client` fixtures and `make_tenant` helper from `tests/conftest.py`. Run with `python -m pytest tests/test_profit_estimation.py -v` from the repo root.
- **Frequent commits:** one commit per task.
- **No new abstractions.** Follow this codebase's existing inline-route-handler, module-level-function style exactly (no service layer, no serializer classes, no ORM event hooks beyond the existing tenant-stamping listener).

---

## Task 1: Data model — `SubscriptionPlan.cost`, `Customer.cost_override`, `MonthlyProfitEstimate` + migration

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (add 2 columns to existing models, add 1 new model class, register in `TENANT_OWNED_MODELS`)
- Create: `C:\Users\InfoCenter\source\repos\delta-net-saas\migrations\versions\d4f8b2a91c6e_add_plan_cost_customer_cost_override_.py`
- Test: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py` (new file)

**Interfaces:**
- Produces `SubscriptionPlan.cost` (Float, default `0.0`) and `SubscriptionPlan.to_dict()['cost']`.
- Produces `Customer.cost_override` (Float, nullable).
- Produces `MonthlyProfitEstimate` (`id, tenant_id, month, estimated_income, estimated_cost, estimated_profit, updated_at`, `.to_dict()`). Later tasks import this class and query/create rows on it.

- [ ] **Step 1: Write the failing test**

Create `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py`:

```python
from datetime import datetime
import app as appmod
from tests.conftest import make_tenant


def test_plan_cost_customer_override_and_monthly_estimate_defaults(app):
    with app.app_context():
        tenant = appmod.Tenant(name="Biz", slug="biz")
        appmod.db.session.add(tenant)
        appmod.db.session.flush()

        plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Fiber 50", price=25.0,
            billing_cycle="monthly", cost=15.0
        )
        appmod.db.session.add(plan)
        appmod.db.session.flush()
        assert plan.to_dict()["cost"] == 15.0

        customer = appmod.Customer(
            tenant_id=tenant.id, name="Cust", phone="1", address="a",
            subscription_plan_id=plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            cost_override=18.0
        )
        appmod.db.session.add(customer)
        appmod.db.session.commit()
        assert customer.cost_override == 18.0

        estimate = appmod.MonthlyProfitEstimate(
            tenant_id=tenant.id, month="2026-01",
            estimated_income=25.0, estimated_cost=18.0, estimated_profit=7.0
        )
        appmod.db.session.add(estimate)
        appmod.db.session.commit()
        d = estimate.to_dict()
        assert d["month"] == "2026-01"
        assert d["estimated_income"] == 25.0
        assert d["estimated_cost"] == 18.0
        assert d["estimated_profit"] == 7.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: FAIL — `TypeError` (unexpected keyword argument `cost`) or `AttributeError: module 'app' has no attribute 'MonthlyProfitEstimate'`.

- [ ] **Step 3: Add `SubscriptionPlan.cost`**

Find this exact code in `app.py`:

```python
class SubscriptionPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    billing_cycle = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(50), default='active') # active, inactive

    customers = db.relationship('Customer', backref='subscription_plan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'price': float(self.price),
            'billing_cycle': self.billing_cycle,
            'status': self.status
        }
```

Replace with:

```python
class SubscriptionPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    cost = db.Column(db.Float, nullable=False, default=0.0)
    billing_cycle = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(50), default='active') # active, inactive

    customers = db.relationship('Customer', backref='subscription_plan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'price': float(self.price),
            'cost': float(self.cost),
            'billing_cycle': self.billing_cycle,
            'status': self.status
        }
```

- [ ] **Step 4: Add `Customer.cost_override`**

Find this exact code in `app.py`:

```python
    balance = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=True)
```

Replace with:

```python
    balance = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    cost_override = db.Column(db.Float, nullable=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=True)
```

- [ ] **Step 5: Add the `MonthlyProfitEstimate` model**

Find this exact code in `app.py` (end of the `SalaryPayment` class, immediately followed by the `Payment` class):

```python
    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'amount': float(self.amount),
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M:%S'),
            'method': self.method,
            'is_advance': self.is_advance,
            'note': self.note
        }

class Payment(db.Model):
```

Replace with:

```python
    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'amount': float(self.amount),
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M:%S'),
            'method': self.method,
            'is_advance': self.is_advance,
            'note': self.note
        }

# --- Estimated vs. real profit: one row per tenant per calendar month.
# Only ever upserted for the CURRENT month (see recalculate_estimated_profit
# below) -- once the calendar rolls into a new month nothing targets last
# month's row again, which is what makes it a frozen historical record.
class MonthlyProfitEstimate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    month = db.Column(db.String(7), nullable=False)  # 'YYYY-MM'
    estimated_income = db.Column(db.Float, nullable=False, default=0.0)
    estimated_cost = db.Column(db.Float, nullable=False, default=0.0)
    estimated_profit = db.Column(db.Float, nullable=False, default=0.0)  # denormalized: income - cost
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('tenant_id', 'month', name='uq_monthly_profit_estimate_tenant_month'),)

    def to_dict(self):
        return {
            'id': self.id,
            'month': self.month,
            'estimated_income': float(self.estimated_income),
            'estimated_cost': float(self.estimated_cost),
            'estimated_profit': float(self.estimated_profit),
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.updated_at else None
        }

class Payment(db.Model):
```

- [ ] **Step 6: Register `MonthlyProfitEstimate` in `TENANT_OWNED_MODELS`**

Find this exact code in `app.py`:

```python
TENANT_OWNED_MODELS = (
    Reseller, ResellerPayment, Customer, SubscriptionPlan, Sector, Supplier,
    SupplierPayment, ExpenseCategory, Expense, Payment, GeneratedReceipt,
    AddonPurchase, BusinessSettings, WhatsAppSettings,
    ServiceStatus, SupportTicket, TicketLog, PushSubscription, ServiceOutage,
    CustomerFeedback, PaymentReminder, UpgradeRequest,
    Employee, SalaryCharge, SalaryPayment,
)
```

Replace with:

```python
TENANT_OWNED_MODELS = (
    Reseller, ResellerPayment, Customer, SubscriptionPlan, Sector, Supplier,
    SupplierPayment, ExpenseCategory, Expense, Payment, GeneratedReceipt,
    AddonPurchase, BusinessSettings, WhatsAppSettings,
    ServiceStatus, SupportTicket, TicketLog, PushSubscription, ServiceOutage,
    CustomerFeedback, PaymentReminder, UpgradeRequest,
    Employee, SalaryCharge, SalaryPayment,
    MonthlyProfitEstimate,
)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: PASS (1 test).

- [ ] **Step 8: Create the migration**

Create `C:\Users\InfoCenter\source\repos\delta-net-saas\migrations\versions\d4f8b2a91c6e_add_plan_cost_customer_cost_override_.py`:

```python
"""add subscription plan cost, customer cost override, monthly profit estimate

Revision ID: d4f8b2a91c6e
Revises: b7e2c4f19a3d
Create Date: 2026-07-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f8b2a91c6e'
down_revision = 'b7e2c4f19a3d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost', sa.Float(), nullable=False, server_default='0.0'))

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost_override', sa.Float(), nullable=True))

    op.create_table('monthly_profit_estimate',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('month', sa.String(length=7), nullable=False),
    sa.Column('estimated_income', sa.Float(), nullable=False),
    sa.Column('estimated_cost', sa.Float(), nullable=False),
    sa.Column('estimated_profit', sa.Float(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], name=op.f('fk_monthly_profit_estimate_tenant_id_tenant')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_monthly_profit_estimate')),
    sa.UniqueConstraint('tenant_id', 'month', name='uq_monthly_profit_estimate_tenant_month')
    )
    with op.batch_alter_table('monthly_profit_estimate', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monthly_profit_estimate_tenant_id'), ['tenant_id'], unique=False)


def downgrade():
    with op.batch_alter_table('monthly_profit_estimate', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_monthly_profit_estimate_tenant_id'))
    op.drop_table('monthly_profit_estimate')

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.drop_column('cost_override')

    with op.batch_alter_table('subscription_plan', schema=None) as batch_op:
        batch_op.drop_column('cost')
```

`server_default='0.0'` on the new NOT NULL `cost` column backfills every existing `subscription_plan` row automatically — this is the standard non-destructive way to add a required column, and touches nothing else on those rows. pytest never runs this file (tests build schema via `db.create_all()`, driven by the model classes from Steps 3-5); if a `JWT_SECRET_KEY` env var and working DB are configured locally, verify with `flask db upgrade`, otherwise this is best-effort and Step 7's pytest pass is the authoritative gate.

- [ ] **Step 9: Commit**

```bash
git add app.py migrations/versions/d4f8b2a91c6e_add_plan_cost_customer_cost_override_.py tests/test_profit_estimation.py
git commit -m "feat(profit-estimation): add SubscriptionPlan.cost, Customer.cost_override, MonthlyProfitEstimate"
```

---

## Task 2: `recalculate_estimated_profit(tenant_id)` computation

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (add the function near the other scheduler-style functions)
- Test: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py` (append)

**Interfaces:**
- Consumes: `Customer`, `SubscriptionPlan`, `MonthlyProfitEstimate` (Task 1).
- Produces: `recalculate_estimated_profit(tenant_id)` — takes an explicit `tenant_id`, never calls `tenant_query(...)`, upserts and commits the current month's `MonthlyProfitEstimate` row, never raises. Later tasks call this after their own commits.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profit_estimation.py`:

```python
def test_recalculate_estimated_profit_amortizes_and_overrides(app):
    with app.app_context():
        tenant = appmod.Tenant(name="Biz", slug="biz")
        appmod.db.session.add(tenant)
        appmod.db.session.flush()

        monthly_plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Monthly50", price=25.0,
            billing_cycle="monthly", cost=15.0
        )
        yearly_plan = appmod.SubscriptionPlan(
            tenant_id=tenant.id, name="Yearly200", price=240.0,
            billing_cycle="yearly", cost=120.0
        )
        appmod.db.session.add_all([monthly_plan, yearly_plan])
        appmod.db.session.flush()

        # Uses the plan's default cost (15.0).
        cust_default_cost = appmod.Customer(
            tenant_id=tenant.id, name="A", phone="1", address="a",
            subscription_plan_id=monthly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            is_subscription_active=True,
        )
        # Overrides cost to 18.0 despite being on the same plan as A.
        cust_override_cost = appmod.Customer(
            tenant_id=tenant.id, name="B", phone="2", address="b",
            subscription_plan_id=monthly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            is_subscription_active=True,
            cost_override=18.0,
        )
        # Yearly plan: price/cost amortized over 12 months.
        cust_yearly = appmod.Customer(
            tenant_id=tenant.id, name="C", phone="3", address="c",
            subscription_plan_id=yearly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2027, 1, 1),
            is_subscription_active=True,
        )
        # Inactive: must not contribute.
        cust_inactive = appmod.Customer(
            tenant_id=tenant.id, name="D", phone="4", address="d",
            subscription_plan_id=monthly_plan.id,
            subscription_start_date=datetime(2026, 1, 1),
            subscription_expiry_date=datetime(2026, 2, 1),
            is_subscription_active=False,
        )
        appmod.db.session.add_all([cust_default_cost, cust_override_cost, cust_yearly, cust_inactive])
        appmod.db.session.commit()

        appmod.recalculate_estimated_profit(tenant.id)

        month = appmod.datetime.utcnow().strftime('%Y-%m')
        estimate = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant.id, month=month).first()
        assert estimate is not None

        # income: 25 (A) + 25 (B) + 240/12=20 (C) = 70
        # cost:   15 (A) + 18 (B) + 120/12=10 (C) = 43
        assert estimate.estimated_income == 70.0
        assert estimate.estimated_cost == 43.0
        assert estimate.estimated_profit == 27.0

        # Calling again upserts the SAME row -- no duplicate for this month.
        appmod.recalculate_estimated_profit(tenant.id)
        assert appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant.id, month=month).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: FAIL with `AttributeError: module 'app' has no attribute 'recalculate_estimated_profit'`.

- [ ] **Step 3: Add `recalculate_estimated_profit`**

Find this exact code in `app.py`:

```python
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error generating missing salary charges: {str(e)}")


# Initialize scheduler
```

Replace with:

```python
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error generating missing salary charges: {str(e)}")


def recalculate_estimated_profit(tenant_id):
    """Recompute the CURRENT month's estimated profit for one tenant from its
    currently active customers. Runs both from request handlers (called after
    their own commit, so a failure here never blocks the primary action) and
    from the scheduler, so it takes an explicit tenant_id and never calls
    tenant_query(). Never raises -- mirrors generate_missing_salary_charges's
    rollback+print pattern."""
    try:
        customers = Customer.query.filter_by(tenant_id=tenant_id, is_subscription_active=True).all()

        estimated_income = 0.0
        estimated_cost = 0.0

        for customer in customers:
            plan = SubscriptionPlan.query.filter_by(tenant_id=tenant_id, id=customer.subscription_plan_id).first()
            if not plan:
                continue

            effective_price = max(0.0, plan.price - (customer.discount or 0.0))
            effective_cost = customer.cost_override if customer.cost_override is not None else plan.cost

            if plan.billing_cycle == 'monthly':
                factor = 1.0
            elif plan.billing_cycle == 'yearly':
                factor = 1.0 / 12
            else:
                continue  # Unrecognized cycle: skip, matches generate_missing_payments

            estimated_income += effective_price * factor
            estimated_cost += effective_cost * factor

        month = datetime.utcnow().strftime('%Y-%m')
        estimate = MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()
        if not estimate:
            estimate = MonthlyProfitEstimate(tenant_id=tenant_id, month=month)
            db.session.add(estimate)

        estimate.estimated_income = estimated_income
        estimate.estimated_cost = estimated_cost
        estimate.estimated_profit = estimated_income - estimated_cost

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error recalculating estimated profit: {str(e)}")


# Initialize scheduler
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_profit_estimation.py
git commit -m "feat(profit-estimation): add recalculate_estimated_profit computation"
```

---

## Task 3: Wire recompute into the customer lifecycle

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (customer list serialization, `add_customer`, `update_customer`, `delete_customer`, `activate_subscription`, `cancel_subscription`)
- Test: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py` (append)

**Interfaces:**
- Consumes: `recalculate_estimated_profit` (Task 2).
- Produces: `cost_override` now appears in `GET /api/customers`' customer list and in `PUT /api/customers/<id>`'s response `customer` object; `POST/PUT /api/customers` accept `cost_override` (blank/`null` clears it back to "use the plan's cost"). Every customer create/edit/cancel/reactivate/delete now keeps the current month's `MonthlyProfitEstimate` row up to date.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profit_estimation.py`:

```python
def test_customer_lifecycle_triggers_recompute(app, client):
    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]

    r2 = client.post("/api/customers", headers=a,
                      json={"name": "Cust", "phone": "1", "address": "a",
                            "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})
    assert r2.status_code == 201
    customer_id = r2.get_json()["customer_id"]

    def _current_estimate():
        with app.app_context():
            month = appmod.datetime.utcnow().strftime('%Y-%m')
            return appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()

    # Adding an active customer triggers a recompute.
    est = _current_estimate()
    assert est is not None
    assert est.estimated_income == 25.0
    assert est.estimated_cost == 15.0

    # Setting a per-customer cost override triggers a recompute.
    r3 = client.put(f"/api/customers/{customer_id}", headers=a, json={"cost_override": 18})
    assert r3.status_code == 200
    assert r3.get_json()["customer"]["cost_override"] == 18.0
    assert _current_estimate().estimated_cost == 18.0

    # Clearing the override falls back to the plan's cost.
    r4 = client.put(f"/api/customers/{customer_id}", headers=a, json={"cost_override": ""})
    assert r4.status_code == 200
    assert r4.get_json()["customer"]["cost_override"] is None
    assert _current_estimate().estimated_cost == 15.0

    # cost_override also appears in the customer list.
    listed = client.get("/api/customers", headers=a).get_json()
    assert listed["customers"][0]["cost_override"] is None

    # Canceling the subscription removes the customer's contribution.
    r5 = client.put(f"/api/customers/{customer_id}/cancel_subscription", headers=a)
    assert r5.status_code == 200
    assert _current_estimate().estimated_income == 0.0

    # Reactivating restores it.
    r6 = client.put(f"/api/customers/{customer_id}/activate_subscription", headers=a)
    assert r6.status_code == 200
    assert _current_estimate().estimated_income == 25.0

    # Deleting the customer also removes their contribution.
    r7 = client.delete(f"/api/customers/{customer_id}", headers=a)
    assert r7.status_code == 200
    assert _current_estimate().estimated_income == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: FAIL — `est` is `None` (no recompute wired yet), or a `KeyError`/`AssertionError` on `cost_override`.

- [ ] **Step 3: Add `cost_override` to the customer list serialization**

Find this exact code in `app.py`:

```python
            customer_dict = {
                'id': c.id,
                'name': c.name,
                'phone': c.phone,
                'address': c.address,
                'subscription_plan_id': c.subscription_plan_id,
                'subscription_start_date': c.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': c.subscription_expiry_date.strftime('%Y-%m-%d') if c.subscription_expiry_date else None,
                'is_subscription_active': c.is_subscription_active,
                'balance': float(c.balance) if c.balance else 0.0,
                'discount': float(c.discount) if c.discount else 0.0,
                'reseller_id': c.reseller_id,
                'subscription_plan': c.subscription_plan.to_dict() if c.subscription_plan else None
            }
```

Replace with:

```python
            customer_dict = {
                'id': c.id,
                'name': c.name,
                'phone': c.phone,
                'address': c.address,
                'subscription_plan_id': c.subscription_plan_id,
                'subscription_start_date': c.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': c.subscription_expiry_date.strftime('%Y-%m-%d') if c.subscription_expiry_date else None,
                'is_subscription_active': c.is_subscription_active,
                'balance': float(c.balance) if c.balance else 0.0,
                'discount': float(c.discount) if c.discount else 0.0,
                'cost_override': float(c.cost_override) if c.cost_override is not None else None,
                'reseller_id': c.reseller_id,
                'subscription_plan': c.subscription_plan.to_dict() if c.subscription_plan else None
            }
```

- [ ] **Step 4: Accept `cost_override` in `add_customer` and recompute after creation**

Find this exact code in `app.py`:

```python
        discount = float(data.get('discount', 0.0))

        # Create new customer first
        new_customer = new_for_tenant(
            Customer,
            name=data['name'],
            phone=data['phone'],
            address=data['address'],
            sector=data.get('sector'),
            subscription_plan_id=data['subscription_plan_id'],
            discount=discount,
            subscription_start_date=subscription_start_date,
            # Expiry date will be set by the payment loop
            subscription_expiry_date=subscription_start_date,
            is_subscription_active=True,
            balance=0.0,
            reseller_id=data.get('reseller_id') if data.get('reseller_id') != "" else None
        )
        db.session.add(new_customer)
        db.session.flush() # Flush to get new_customer.id
```

Replace with:

```python
        discount = float(data.get('discount', 0.0))
        raw_cost_override = data.get('cost_override')
        cost_override = float(raw_cost_override) if raw_cost_override not in (None, '') else None

        # Create new customer first
        new_customer = new_for_tenant(
            Customer,
            name=data['name'],
            phone=data['phone'],
            address=data['address'],
            sector=data.get('sector'),
            subscription_plan_id=data['subscription_plan_id'],
            discount=discount,
            cost_override=cost_override,
            subscription_start_date=subscription_start_date,
            # Expiry date will be set by the payment loop
            subscription_expiry_date=subscription_start_date,
            is_subscription_active=True,
            balance=0.0,
            reseller_id=data.get('reseller_id') if data.get('reseller_id') != "" else None
        )
        db.session.add(new_customer)
        db.session.flush() # Flush to get new_customer.id
```

Find this exact code in `app.py`:

```python
        # --- ADDED: Reconcile balance after creating customer and all initial charges ---
        apply_customer_balance_to_unpaid_payments(new_customer)

        db.session.commit()
        
        # Send WhatsApp Notification for Subscription Creation
```

Replace with:

```python
        # --- ADDED: Reconcile balance after creating customer and all initial charges ---
        apply_customer_balance_to_unpaid_payments(new_customer)

        db.session.commit()
        recalculate_estimated_profit(new_customer.tenant_id)
        
        # Send WhatsApp Notification for Subscription Creation
```

- [ ] **Step 5: Accept `cost_override` in `update_customer`, recompute after every update**

Find this exact code in `app.py`:

```python
        if 'discount' in data:
            customer.discount = float(data['discount'])
        if 'balance' in data:
            customer.balance = float(data['balance'])
```

Replace with:

```python
        if 'discount' in data:
            customer.discount = float(data['discount'])
        if 'cost_override' in data:
            customer.cost_override = float(data['cost_override']) if data['cost_override'] not in (None, '') else None
        if 'balance' in data:
            customer.balance = float(data['balance'])
```

Find this exact code in `app.py`:

```python
        db.session.commit()
        
        return jsonify({
            'message': 'Customer updated successfully!',
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone,
                'address': customer.address,
                'subscription_plan_id': customer.subscription_plan_id,
                'discount': float(customer.discount),
                'subscription_start_date': customer.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None,
                'is_subscription_active': customer.is_subscription_active,
                'balance': float(customer.balance)
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
@jwt_required()
def delete_customer(customer_id):
    try:
        customer = tenant_query(Customer).filter_by(id=customer_id).first()
        if not customer:
            return jsonify({'message': 'Customer not found!'}), 404
        
        # The 'cascade' option in the model will handle deleting related records
        db.session.delete(customer)
        db.session.commit()
        
        return jsonify({'message': 'Customer and all related data deleted successfully!'}), 200
```

Replace with:

```python
        db.session.commit()
        recalculate_estimated_profit(customer.tenant_id)
        
        return jsonify({
            'message': 'Customer updated successfully!',
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone,
                'address': customer.address,
                'subscription_plan_id': customer.subscription_plan_id,
                'discount': float(customer.discount),
                'cost_override': float(customer.cost_override) if customer.cost_override is not None else None,
                'subscription_start_date': customer.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None,
                'is_subscription_active': customer.is_subscription_active,
                'balance': float(customer.balance)
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
@jwt_required()
def delete_customer(customer_id):
    try:
        customer = tenant_query(Customer).filter_by(id=customer_id).first()
        if not customer:
            return jsonify({'message': 'Customer not found!'}), 404
        
        tenant_id = customer.tenant_id
        # The 'cascade' option in the model will handle deleting related records
        db.session.delete(customer)
        db.session.commit()
        recalculate_estimated_profit(tenant_id)
        
        return jsonify({'message': 'Customer and all related data deleted successfully!'}), 200
```

- [ ] **Step 6: Recompute after `activate_subscription` and `cancel_subscription`**

Find this exact code in `app.py`:

```python
@app.route('/api/customers/<int:customer_id>/activate_subscription', methods=['PUT'])
@jwt_required()
def activate_subscription(customer_id):
    customer = tenant_query(Customer).filter_by(id=customer_id).first()
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Check if the subscription is already active
    if customer.is_subscription_active:
        return jsonify({'message': 'Subscription is already active!'}), 400

    try:
        subscription_plan = tenant_query(SubscriptionPlan).filter_by(id=customer.subscription_plan_id).first()
        if not subscription_plan:
            return jsonify({'message': 'Subscription plan not found for customer!'}), 404

        now = datetime.utcnow()
        is_expired = not customer.subscription_expiry_date or customer.subscription_expiry_date < now

        customer.is_subscription_active = True

        if is_expired:
            if subscription_plan.billing_cycle == 'monthly':
                new_expiry_date = now + relativedelta(months=1)
            elif subscription_plan.billing_cycle == 'yearly':
                new_expiry_date = now + relativedelta(years=1)
            else:
                new_expiry_date = now + relativedelta(months=1)

            customer.subscription_expiry_date = new_expiry_date

            amount_due = subscription_plan.price - (customer.discount or 0.0)
            if amount_due < 0:
                amount_due = 0.0

            already_billed = (
                has_pending_reseller_charge(customer.id, new_expiry_date, customer.tenant_id) if customer.reseller_id
                else has_pending_payment(customer.id, new_expiry_date, customer.tenant_id)
            )
            if amount_due > 0 and not already_billed:
                if customer.reseller_id:
                    reseller = tenant_query(Reseller).filter_by(id=customer.reseller_id).first()
                    if reseller:
                        reseller.balance += amount_due
                        reseller_payment = ResellerPayment(
                            reseller_id=reseller.id,
                            customer_id=customer.id,
                            amount=amount_due,
                            type='credit_added',
                            date=new_expiry_date,
                            description=f'Reactivation for customer {customer.name}'
                        )
                        db.session.add(reseller_payment)
                else:
                    new_payment = Payment(
                        customer_id=customer.id,
                        amount=amount_due,
                        paid=False,
                        date=now,
                        pre_payment=False
                    )
                    db.session.add(new_payment)
                    customer.balance -= amount_due

        db.session.commit()

        # ── Send WhatsApp notification (API mode) ──────────────────────────────
        try:
            if customer.subscription_expiry_date:
                send_whatsapp_message(
                    customer,
                    event_type='subscription_renewed',
                    context={'expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d')}
                )
        except Exception as wa_error:
            logging.error(f"Failed to send WA message on activate: {wa_error}")
        # ──────────────────────────────────────────────────────────────────────

        expiry_str = customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None
        return jsonify({
            'message': 'Subscription activated successfully!',
            'subscription_expiry_date': expiry_str
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400

@app.route('/api/customers/<int:customer_id>/cancel_subscription', methods=['PUT'])
@jwt_required()
def cancel_subscription(customer_id):
    customer = tenant_query(Customer).filter_by(id=customer_id).first()
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Check if the subscription is already canceled
    if not customer.is_subscription_active:
        return jsonify({'message': 'Subscription is already canceled!'}), 400

    try:
        # Mark the subscription as inactive
        customer.is_subscription_active = False
        
        db.session.commit()

        expiry_str = customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None
        return jsonify({
            'message': 'Subscription canceled successfully!',
            'subscription_expiry_date': expiry_str
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400
```

Replace with:

```python
@app.route('/api/customers/<int:customer_id>/activate_subscription', methods=['PUT'])
@jwt_required()
def activate_subscription(customer_id):
    customer = tenant_query(Customer).filter_by(id=customer_id).first()
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Check if the subscription is already active
    if customer.is_subscription_active:
        return jsonify({'message': 'Subscription is already active!'}), 400

    try:
        subscription_plan = tenant_query(SubscriptionPlan).filter_by(id=customer.subscription_plan_id).first()
        if not subscription_plan:
            return jsonify({'message': 'Subscription plan not found for customer!'}), 404

        now = datetime.utcnow()
        is_expired = not customer.subscription_expiry_date or customer.subscription_expiry_date < now

        customer.is_subscription_active = True

        if is_expired:
            if subscription_plan.billing_cycle == 'monthly':
                new_expiry_date = now + relativedelta(months=1)
            elif subscription_plan.billing_cycle == 'yearly':
                new_expiry_date = now + relativedelta(years=1)
            else:
                new_expiry_date = now + relativedelta(months=1)

            customer.subscription_expiry_date = new_expiry_date

            amount_due = subscription_plan.price - (customer.discount or 0.0)
            if amount_due < 0:
                amount_due = 0.0

            already_billed = (
                has_pending_reseller_charge(customer.id, new_expiry_date, customer.tenant_id) if customer.reseller_id
                else has_pending_payment(customer.id, new_expiry_date, customer.tenant_id)
            )
            if amount_due > 0 and not already_billed:
                if customer.reseller_id:
                    reseller = tenant_query(Reseller).filter_by(id=customer.reseller_id).first()
                    if reseller:
                        reseller.balance += amount_due
                        reseller_payment = ResellerPayment(
                            reseller_id=reseller.id,
                            customer_id=customer.id,
                            amount=amount_due,
                            type='credit_added',
                            date=new_expiry_date,
                            description=f'Reactivation for customer {customer.name}'
                        )
                        db.session.add(reseller_payment)
                else:
                    new_payment = Payment(
                        customer_id=customer.id,
                        amount=amount_due,
                        paid=False,
                        date=now,
                        pre_payment=False
                    )
                    db.session.add(new_payment)
                    customer.balance -= amount_due

        db.session.commit()
        recalculate_estimated_profit(customer.tenant_id)

        # ── Send WhatsApp notification (API mode) ──────────────────────────────
        try:
            if customer.subscription_expiry_date:
                send_whatsapp_message(
                    customer,
                    event_type='subscription_renewed',
                    context={'expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d')}
                )
        except Exception as wa_error:
            logging.error(f"Failed to send WA message on activate: {wa_error}")
        # ──────────────────────────────────────────────────────────────────────

        expiry_str = customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None
        return jsonify({
            'message': 'Subscription activated successfully!',
            'subscription_expiry_date': expiry_str
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400

@app.route('/api/customers/<int:customer_id>/cancel_subscription', methods=['PUT'])
@jwt_required()
def cancel_subscription(customer_id):
    customer = tenant_query(Customer).filter_by(id=customer_id).first()
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Check if the subscription is already canceled
    if not customer.is_subscription_active:
        return jsonify({'message': 'Subscription is already canceled!'}), 400

    try:
        # Mark the subscription as inactive
        customer.is_subscription_active = False
        
        db.session.commit()
        recalculate_estimated_profit(customer.tenant_id)

        expiry_str = customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None
        return jsonify({
            'message': 'Subscription canceled successfully!',
            'subscription_expiry_date': expiry_str
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_profit_estimation.py
git commit -m "feat(profit-estimation): wire recompute into the customer lifecycle"
```

---

## Task 4: Subscription plan cost API + recompute on plan edit

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (`add_subscription_plan`, `update_subscription_plan`)
- Test: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py` (append)

**Interfaces:**
- Consumes: `recalculate_estimated_profit` (Task 2).
- Produces: `POST/PUT /api/subscription_plans` accept `cost`. Editing a plan's `cost` (or `price`) recomputes every affected tenant's current-month estimate.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profit_estimation.py`:

```python
def test_subscription_plan_cost_field_and_recompute_on_edit(app, client):
    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    assert r.status_code in (200, 201)
    plan = r.get_json()["plan"]
    assert plan["cost"] == 15.0

    r2 = client.post("/api/customers", headers=a,
                      json={"name": "Cust", "phone": "1", "address": "a",
                            "subscription_plan_id": plan["id"], "subscription_start_date": "2026-01-01"})
    assert r2.status_code == 201

    with app.app_context():
        month = appmod.datetime.utcnow().strftime('%Y-%m')
        estimate = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()
        assert estimate is not None
        assert estimate.estimated_cost == 15.0

    r3 = client.put(f"/api/subscription_plans/{plan['id']}", headers=a, json={"cost": 20})
    assert r3.status_code == 200
    assert r3.get_json()["plan"]["cost"] == 20.0

    with app.app_context():
        month = appmod.datetime.utcnow().strftime('%Y-%m')
        estimate = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first()
        assert estimate.estimated_cost == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: FAIL — `plan["cost"]` is `0.0` (input ignored) or `KeyError`, and the estimate after the PUT is unchanged.

- [ ] **Step 3: Accept `cost` in `add_subscription_plan` and `update_subscription_plan`, recompute on update**

Find this exact code in `app.py`:

```python
        new_plan = SubscriptionPlan(
            name=data['name'],
            price=price,
            billing_cycle=data['billing_cycle'],
            status=data.get('status', 'active')
        )
```

Replace with:

```python
        new_plan = SubscriptionPlan(
            name=data['name'],
            price=price,
            cost=float(data.get('cost', 0) or 0),
            billing_cycle=data['billing_cycle'],
            status=data.get('status', 'active')
        )
```

Find this exact code in `app.py`:

```python
def update_subscription_plan(plan_id):
    try:
        plan = tenant_query(SubscriptionPlan).filter_by(id=plan_id).first()
        if not plan:
            return jsonify({'message': 'Subscription plan not found!'}), 404
        
        data = request.json
        plan.name = data.get('name', plan.name)
        plan.price = float(data.get('price', plan.price))
        plan.billing_cycle = data.get('billing_cycle', plan.billing_cycle)
        plan.status = data.get('status', plan.status)

        db.session.commit()
        return jsonify({'message': 'Subscription plan updated successfully!', 'plan': plan.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400
```

Replace with:

```python
def update_subscription_plan(plan_id):
    try:
        plan = tenant_query(SubscriptionPlan).filter_by(id=plan_id).first()
        if not plan:
            return jsonify({'message': 'Subscription plan not found!'}), 404
        
        data = request.json
        plan.name = data.get('name', plan.name)
        plan.price = float(data.get('price', plan.price))
        plan.cost = float(data.get('cost', plan.cost))
        plan.billing_cycle = data.get('billing_cycle', plan.billing_cycle)
        plan.status = data.get('status', plan.status)

        db.session.commit()
        recalculate_estimated_profit(plan.tenant_id)
        return jsonify({'message': 'Subscription plan updated successfully!', 'plan': plan.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400
```

Note: `update_customer` already unconditionally calls `recalculate_estimated_profit` after every commit (Task 3), so a customer's plan switch (`subscription_plan_id` change) is already covered — no separate handling is needed here.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_profit_estimation.py
git commit -m "feat(profit-estimation): add subscription plan cost field and recompute on edit"
```

---

## Task 5: Daily scheduler safety net

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (add a third job to the existing `BackgroundScheduler`)
- Test: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py` (append)

**Interfaces:**
- Consumes: `recalculate_estimated_profit` (Task 2), `Tenant` (existing).
- Produces: `recalculate_all_estimated_profits_with_context()` — iterates every active tenant and recomputes its current-month estimate; wired into the scheduler alongside the billing and payroll jobs.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profit_estimation.py`:

```python
def test_daily_recompute_covers_quiet_tenants_without_events(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    b = make_tenant(client, "Biz B", "b_admin")

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})

    with app.app_context():
        a_tid = appmod.Tenant.query.filter_by(slug="biz-a").first().id
        b_tid = appmod.Tenant.query.filter_by(slug="biz-b").first().id
        month = appmod.datetime.utcnow().strftime('%Y-%m')

        # Wipe out the estimate the add_customer trigger already created, to
        # prove the daily job -- not the earlier event -- recreates it below.
        appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=a_tid, month=month).delete()
        appmod.db.session.commit()
        assert appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=a_tid, month=month).first() is None

    # Runs with NO request context, exactly like a real scheduler tick.
    appmod.recalculate_all_estimated_profits_with_context()

    with app.app_context():
        estimate_a = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=a_tid, month=month).first()
        assert estimate_a is not None
        assert estimate_a.estimated_income == 25.0

        # Tenant B has no customers/plans at all -- the job must not error on
        # it, and every row must stay under its own tenant.
        estimate_b = appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=b_tid, month=month).first()
        assert estimate_b is not None
        assert estimate_b.estimated_income == 0.0
        assert appmod.MonthlyProfitEstimate.query.filter(appmod.MonthlyProfitEstimate.tenant_id.is_(None)).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: FAIL with `AttributeError: module 'app' has no attribute 'recalculate_all_estimated_profits_with_context'`.

- [ ] **Step 3: Add the daily job and wire it into the scheduler**

Find this exact code in `app.py`:

```python
# Initialize scheduler
scheduler = BackgroundScheduler(daemon=True, executors={'default': {'type': 'threadpool', 'max_workers': 1}})

def generate_missing_payments_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            generate_missing_payments(t.id)

def generate_missing_salary_charges_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            generate_missing_salary_charges(t.id)

# Start the scheduler in ONE runner only. Under multiple gunicorn workers, an
# in-process scheduler would fire the daily jobs once per worker; run exactly one
# process/container with RUN_SCHEDULER=1. Defaults on for single-process dev.
if os.environ.get("RUN_SCHEDULER", "1") == "1" and not scheduler.running:
    scheduler.add_job(func=generate_missing_payments_with_context, trigger="interval", days=1)
    scheduler.add_job(func=generate_missing_salary_charges_with_context, trigger="interval", days=1)
    scheduler.start()
```

Replace with:

```python
# Initialize scheduler
scheduler = BackgroundScheduler(daemon=True, executors={'default': {'type': 'threadpool', 'max_workers': 1}})

def generate_missing_payments_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            generate_missing_payments(t.id)

def generate_missing_salary_charges_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            generate_missing_salary_charges(t.id)

def recalculate_all_estimated_profits_with_context():
    with app.app_context():
        for t in Tenant.query.filter_by(status="active").all():
            recalculate_estimated_profit(t.id)

# Start the scheduler in ONE runner only. Under multiple gunicorn workers, an
# in-process scheduler would fire the daily jobs once per worker; run exactly one
# process/container with RUN_SCHEDULER=1. Defaults on for single-process dev.
if os.environ.get("RUN_SCHEDULER", "1") == "1" and not scheduler.running:
    scheduler.add_job(func=generate_missing_payments_with_context, trigger="interval", days=1)
    scheduler.add_job(func=generate_missing_salary_charges_with_context, trigger="interval", days=1)
    scheduler.add_job(func=recalculate_all_estimated_profits_with_context, trigger="interval", days=1)
    scheduler.start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_profit_estimation.py
git commit -m "feat(profit-estimation): add daily scheduler safety net"
```

---

## Task 6: Fold estimated profit + variance into the Financial Report API

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\app.py` (`get_financial_report`)
- Test: `C:\Users\InfoCenter\source\repos\delta-net-saas\tests\test_profit_estimation.py` (append)

**Interfaces:**
- Consumes: `MonthlyProfitEstimate`, `recalculate_estimated_profit` (Tasks 1-2).
- Produces: `GET /api/reports/financial`'s `monthly_data` rows gain `estimated_profit` (nullable) and `variance` (nullable, `= profit - estimated_profit`); `totals` gains `estimated_profit` and `variance` (summed only over months that have a value).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_profit_estimation.py`:

```python
def test_financial_report_includes_estimated_profit_and_variance(app, client):
    from datetime import datetime, timedelta

    a = make_tenant(client, "Biz A", "a_admin")

    with app.app_context():
        tenant_id = appmod.Tenant.query.filter_by(slug="biz-a").first().id

    r = client.post("/api/subscription_plans", headers=a,
                     json={"name": "Fiber 50", "price": 25, "billing_cycle": "monthly", "cost": 15})
    plan_id = r.get_json()["plan"]["id"]
    client.post("/api/customers", headers=a,
                json={"name": "Cust", "phone": "1", "address": "a",
                      "subscription_plan_id": plan_id, "subscription_start_date": "2026-01-01"})

    # Delete the estimate row that add_customer's trigger already created, so
    # the assertions below can only pass via the endpoint's OWN lazy
    # current-month backfill, not a row left over from an earlier trigger.
    with app.app_context():
        month = appmod.datetime.utcnow().strftime('%Y-%m')
        appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).delete()
        appmod.db.session.commit()
        assert appmod.MonthlyProfitEstimate.query.filter_by(tenant_id=tenant_id, month=month).first() is None

    start = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z')
    end = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z')
    fin = client.get(f"/api/reports/financial?start_date={start}&end_date={end}", headers=a).get_json()

    assert len(fin["monthly_data"]) == 1
    row = fin["monthly_data"][0]
    # No real cash income/expenses recorded this month, but the estimate
    # (25 income - 15 cost = 10 profit) is present via the lazy current-month backfill.
    assert row["income"] == 0.0
    assert row["profit"] == 0.0
    assert row["estimated_profit"] == 10.0
    assert row["variance"] == -10.0  # real (0) - estimated (10)
    assert fin["totals"]["estimated_profit"] == 10.0
    assert fin["totals"]["variance"] == -10.0


def test_financial_report_past_month_with_no_estimate_is_null(client):
    from datetime import datetime, timedelta

    a = make_tenant(client, "Biz A", "a_admin")

    # A far-past range that predates this feature entirely: no MonthlyProfitEstimate
    # row will ever exist for it, and it is NOT the current month, so there is no
    # lazy backfill either.
    start = "2020-01-01T00:00:00Z"
    end = "2020-01-31T23:59:59Z"

    # Force a real cash data point in that month so it actually appears as a row.
    client.post("/api/expense_categories", headers=a, json={"name": "Rent"})
    client.post("/api/expenses", headers=a,
                json={"category": "Rent", "amount": 50, "description": "Office rent",
                      "date": "2020-01-15", "is_credit": False})

    fin = client.get(f"/api/reports/financial?start_date={start}&end_date={end}", headers=a).get_json()
    assert len(fin["monthly_data"]) == 1
    row = fin["monthly_data"][0]
    assert row["estimated_profit"] is None
    assert row["variance"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: FAIL — `KeyError: 'estimated_profit'`.

- [ ] **Step 3: Extend `get_financial_report`**

Find this exact code in `app.py`:

```python
@app.route('/api/reports/financial', methods=['GET'])
@jwt_required()
def get_financial_report():
    """
    Get Income, Expenses, and Profit aggregated by month for a given date range.
    """
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return jsonify({'error': 'start_date and end_date are required'}), 400

        # Basic parsing stripping 'Z' if present
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = end_date.replace(hour=23, minute=59, second=59)

        # 1. Income: Payments marked as paid. Fall back to date if paid_at is null.
        income_query = db.session.query(
            func.strftime('%Y-%m', func.coalesce(Payment.paid_at, Payment.date)).label('month'),
            func.sum(Payment.amount).label('total')
        ).filter(
            Payment.tenant_id == current_tenant_id(),
            Payment.paid == True,
            func.coalesce(Payment.paid_at, Payment.date) >= start_date,
            func.coalesce(Payment.paid_at, Payment.date) <= end_date
        ).group_by('month').all()

        # 2. Expenses (direct non-credit)
        expense_query = db.session.query(
            func.strftime('%Y-%m', Expense.date).label('month'),
            func.sum(Expense.amount).label('total')
        ).filter(
            Expense.tenant_id == current_tenant_id(),
            Expense.is_credit == False,
            Expense.date >= start_date,
            Expense.date <= end_date
        ).group_by('month').all()

        # 3. Supplier cash payments
        sp_query = db.session.query(
            func.strftime('%Y-%m', SupplierPayment.payment_date).label('month'),
            func.sum(SupplierPayment.amount).label('total')
        ).filter(
            SupplierPayment.tenant_id == current_tenant_id(),
            SupplierPayment.payment_date >= start_date,
            SupplierPayment.payment_date <= end_date
        ).group_by('month').all()

        # 4. Salary cash payments
        sal_query = db.session.query(
            func.strftime('%Y-%m', SalaryPayment.payment_date).label('month'),
            func.sum(SalaryPayment.amount).label('total')
        ).filter(
            SalaryPayment.tenant_id == current_tenant_id(),
            SalaryPayment.payment_date >= start_date,
            SalaryPayment.payment_date <= end_date
        ).group_by('month').all()

        # Combine results
        months_set = set(
            [row.month for row in income_query] + [row.month for row in expense_query]
            + [row.month for row in sp_query] + [row.month for row in sal_query]
        )

        monthly_data_dict = {m: {'month': m, 'income': 0.0, 'expenses': 0.0, 'profit': 0.0} for m in months_set}

        for row in income_query:
            monthly_data_dict[row.month]['income'] += float(row.total or 0)

        for row in expense_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        for row in sp_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        for row in sal_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        monthly_data = []
        total_income = 0.0
        total_expenses = 0.0

        for m in sorted(months_set):
            data = monthly_data_dict[m]
            data['profit'] = data['income'] - data['expenses']
            monthly_data.append(data)
            
            total_income += data['income']
            total_expenses += data['expenses']

        total_profit = total_income - total_expenses

        return jsonify({
            'monthly_data': monthly_data,
            'totals': {
                'income': total_income,
                'expenses': total_expenses,
                'profit': total_profit
            }
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

Replace with:

```python
@app.route('/api/reports/financial', methods=['GET'])
@jwt_required()
def get_financial_report():
    """
    Get Income, Expenses, and Profit aggregated by month for a given date range.
    """
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return jsonify({'error': 'start_date and end_date are required'}), 400

        # Basic parsing stripping 'Z' if present
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = end_date.replace(hour=23, minute=59, second=59)

        # 1. Income: Payments marked as paid. Fall back to date if paid_at is null.
        income_query = db.session.query(
            func.strftime('%Y-%m', func.coalesce(Payment.paid_at, Payment.date)).label('month'),
            func.sum(Payment.amount).label('total')
        ).filter(
            Payment.tenant_id == current_tenant_id(),
            Payment.paid == True,
            func.coalesce(Payment.paid_at, Payment.date) >= start_date,
            func.coalesce(Payment.paid_at, Payment.date) <= end_date
        ).group_by('month').all()

        # 2. Expenses (direct non-credit)
        expense_query = db.session.query(
            func.strftime('%Y-%m', Expense.date).label('month'),
            func.sum(Expense.amount).label('total')
        ).filter(
            Expense.tenant_id == current_tenant_id(),
            Expense.is_credit == False,
            Expense.date >= start_date,
            Expense.date <= end_date
        ).group_by('month').all()

        # 3. Supplier cash payments
        sp_query = db.session.query(
            func.strftime('%Y-%m', SupplierPayment.payment_date).label('month'),
            func.sum(SupplierPayment.amount).label('total')
        ).filter(
            SupplierPayment.tenant_id == current_tenant_id(),
            SupplierPayment.payment_date >= start_date,
            SupplierPayment.payment_date <= end_date
        ).group_by('month').all()

        # 4. Salary cash payments
        sal_query = db.session.query(
            func.strftime('%Y-%m', SalaryPayment.payment_date).label('month'),
            func.sum(SalaryPayment.amount).label('total')
        ).filter(
            SalaryPayment.tenant_id == current_tenant_id(),
            SalaryPayment.payment_date >= start_date,
            SalaryPayment.payment_date <= end_date
        ).group_by('month').all()

        # 5. Logged estimated profit -- lazily backfill the current month if it's
        # in range and hasn't been computed yet (no event/daily-job tick reached
        # it yet), so the current month is never blank.
        current_month = datetime.utcnow().strftime('%Y-%m')
        start_month = start_date.strftime('%Y-%m')
        end_month = end_date.strftime('%Y-%m')
        if start_month <= current_month <= end_month:
            if not MonthlyProfitEstimate.query.filter_by(
                tenant_id=current_tenant_id(), month=current_month
            ).first():
                recalculate_estimated_profit(current_tenant_id())

        estimate_query = MonthlyProfitEstimate.query.filter(
            MonthlyProfitEstimate.tenant_id == current_tenant_id(),
            MonthlyProfitEstimate.month >= start_month,
            MonthlyProfitEstimate.month <= end_month
        ).all()
        estimate_data = {e.month: e.estimated_profit for e in estimate_query}

        # Combine results
        months_set = set(
            [row.month for row in income_query] + [row.month for row in expense_query]
            + [row.month for row in sp_query] + [row.month for row in sal_query]
            + list(estimate_data.keys())
        )

        monthly_data_dict = {m: {'month': m, 'income': 0.0, 'expenses': 0.0, 'profit': 0.0} for m in months_set}

        for row in income_query:
            monthly_data_dict[row.month]['income'] += float(row.total or 0)

        for row in expense_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        for row in sp_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        for row in sal_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        monthly_data = []
        total_income = 0.0
        total_expenses = 0.0
        total_estimated_profit = 0.0
        total_variance = 0.0

        for m in sorted(months_set):
            data = monthly_data_dict[m]
            data['profit'] = data['income'] - data['expenses']

            estimated_profit = estimate_data.get(m)
            data['estimated_profit'] = estimated_profit
            data['variance'] = (data['profit'] - estimated_profit) if estimated_profit is not None else None

            monthly_data.append(data)

            total_income += data['income']
            total_expenses += data['expenses']
            if estimated_profit is not None:
                total_estimated_profit += estimated_profit
                total_variance += data['variance']

        total_profit = total_income - total_expenses

        return jsonify({
            'monthly_data': monthly_data,
            'totals': {
                'income': total_income,
                'expenses': total_expenses,
                'profit': total_profit,
                'estimated_profit': total_estimated_profit,
                'variance': total_variance
            }
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
```

`MonthlyProfitEstimate.month` is a plain `'YYYY-MM'` string; lexicographic string comparison (`>=`/`<=`) sorts identically to date comparison for that fixed-width format, so filtering by `start_month`/`end_month` strings is correct.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_profit_estimation.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `python -m pytest tests/ -v`
Expected: all tests PASS (existing `get_financial_report` consumers are unaffected since `estimated_profit`/`variance` are new, additive fields).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_profit_estimation.py
git commit -m "feat(profit-estimation): fold estimated profit and variance into the financial report"
```

---

## Task 7: Frontend — plan cost, customer cost override, report comparison

**Files:**
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\frontend\src\components\SubscriptionPlanForm.js`
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\frontend\src\components\SubscriptionsView.js`
- Modify: `C:\Users\InfoCenter\source\repos\delta-net-saas\frontend\src\components\EnhancedReportsView.js`

**Interfaces:**
- Consumes: `cost`/`cost_override` fields (Tasks 1, 3, 4), `estimated_profit`/`variance` fields (Task 6). No `AppContext.js` changes needed — `addSubscriptionPlan`/`updateSubscriptionPlan`/`addCustomer`/`updateCustomer`/`fetchFinancialReport` are already generic passthroughs.

- [ ] **Step 1: Restore the Cost field on the Subscription Plan form**

Find this exact code in `frontend/src/components/SubscriptionPlanForm.js`:

```js
  const [formData, setFormData] = useState({
    name: '',
    price: '',
    billing_cycle: 'monthly', // Default value
    status: 'active'
  });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (plan) {
      setFormData({
        name: plan.name || '',
        price: plan.price || '',
        billing_cycle: plan.billing_cycle || 'monthly',
        status: plan.status || 'active'
      });
    } else {
      setFormData({ // Reset for new plan
        name: '',
        price: '',
        billing_cycle: 'monthly',
        status: 'active'
      });
    }
  }, [plan]);
```

Replace with:

```js
  const [formData, setFormData] = useState({
    name: '',
    price: '',
    cost: '',
    billing_cycle: 'monthly', // Default value
    status: 'active'
  });
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (plan) {
      setFormData({
        name: plan.name || '',
        price: plan.price || '',
        cost: plan.cost || '',
        billing_cycle: plan.billing_cycle || 'monthly',
        status: plan.status || 'active'
      });
    } else {
      setFormData({ // Reset for new plan
        name: '',
        price: '',
        cost: '',
        billing_cycle: 'monthly',
        status: 'active'
      });
    }
  }, [plan]);
```

Find this exact code in `frontend/src/components/SubscriptionPlanForm.js`:

```js
      const dataToSend = {
        ...formData,
        // Ensure price is always a valid number, default to 0.0 if empty/NaN
        price: parseFloat(formData.price) || 0.0,
      };
```

Replace with:

```js
      const dataToSend = {
        ...formData,
        // Ensure price/cost are always valid numbers, default to 0.0 if empty/NaN
        price: parseFloat(formData.price) || 0.0,
        cost: parseFloat(formData.cost) || 0.0,
      };
```

Find this exact code in `frontend/src/components/SubscriptionPlanForm.js`:

```js
      <TextField
        label="Price"
        name="price"
        type="number"
        value={formData.price}
        onChange={handleChange}
        fullWidth
        margin="normal"
        required
        InputProps={{
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
        }}
      />
      {/* Removed Cost TextField */}
```

Replace with:

```js
      <TextField
        label="Price"
        name="price"
        type="number"
        value={formData.price}
        onChange={handleChange}
        fullWidth
        margin="normal"
        required
        InputProps={{
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
        }}
      />
      <TextField
        label="Cost"
        name="cost"
        type="number"
        value={formData.cost}
        onChange={handleChange}
        fullWidth
        margin="normal"
        helperText="What this plan actually costs you to deliver (used for the estimated profit report)"
        InputProps={{
          startAdornment: <InputAdornment position="start">$</InputAdornment>,
        }}
      />
```

- [ ] **Step 2: Add a Cost Override field to the Customer add form**

Find this exact code in `frontend/src/components/SubscriptionsView.js`:

```js
    const [newCustomer, setNewCustomer] = useState({
        name: '',
        phone: '',
        address: '',
        subscription_plan_id: '',
        reseller_id: '',
        discount: 0.0,
        subscription_start_date: new Date().toISOString().split('T')[0],
        additional_payment_amount: 0.0,
    });
```

Replace with:

```js
    const [newCustomer, setNewCustomer] = useState({
        name: '',
        phone: '',
        address: '',
        subscription_plan_id: '',
        reseller_id: '',
        discount: 0.0,
        cost_override: '',
        subscription_start_date: new Date().toISOString().split('T')[0],
        additional_payment_amount: 0.0,
    });
```

Find this exact code in `frontend/src/components/SubscriptionsView.js`:

```js
            setNewCustomer({ name: '', phone: '', address: '', sector: '', subscription_plan_id: '', discount: 0.0, subscription_start_date: new Date().toISOString().split('T')[0], additional_payment_amount: 0.0 });
```

Replace with:

```js
            setNewCustomer({ name: '', phone: '', address: '', sector: '', subscription_plan_id: '', discount: 0.0, cost_override: '', subscription_start_date: new Date().toISOString().split('T')[0], additional_payment_amount: 0.0 });
```

Find this exact code in `frontend/src/components/SubscriptionsView.js`:

```js
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount (Fixed Amount)" value={newCustomer.discount} onChange={(e) => setNewCustomer({ ...newCustomer, discount: parseFloat(e.target.value) || 0.0 })} /></Grid>
```

Replace with:

```js
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount (Fixed Amount)" value={newCustomer.discount} onChange={(e) => setNewCustomer({ ...newCustomer, discount: parseFloat(e.target.value) || 0.0 })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Cost Override (Optional)" value={newCustomer.cost_override} onChange={(e) => setNewCustomer({ ...newCustomer, cost_override: e.target.value })} helperText="Leave blank to use the plan's default cost" /></Grid>
```

- [ ] **Step 3: Add a Cost Override field to the Customer edit dialog**

Find this exact code in `frontend/src/components/SubscriptionsView.js`:

```js
            const response = await apiService.updateCustomer(editingCustomer.id, {
                name: editingCustomer.name,
                phone: editingCustomer.phone,
                address: editingCustomer.address,
                sector: editingCustomer.sector,
                subscription_plan_id: editingCustomer.subscription_plan_id,
                discount: editingCustomer.discount,
                balance: editingCustomer.balance !== undefined ? editingCustomer.balance : 0,
                reseller_id: editingCustomer.reseller_id || ""
            });
```

Replace with:

```js
            const response = await apiService.updateCustomer(editingCustomer.id, {
                name: editingCustomer.name,
                phone: editingCustomer.phone,
                address: editingCustomer.address,
                sector: editingCustomer.sector,
                subscription_plan_id: editingCustomer.subscription_plan_id,
                discount: editingCustomer.discount,
                cost_override: editingCustomer.cost_override,
                balance: editingCustomer.balance !== undefined ? editingCustomer.balance : 0,
                reseller_id: editingCustomer.reseller_id || ""
            });
```

Find this exact code in `frontend/src/components/SubscriptionsView.js`:

```js
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount ($)" value={editingCustomer?.discount || 0} onChange={(e) => setEditingCustomer({ ...editingCustomer, discount: parseFloat(e.target.value) || 0 })} /></Grid>
```

Replace with:

```js
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount ($)" value={editingCustomer?.discount || 0} onChange={(e) => setEditingCustomer({ ...editingCustomer, discount: parseFloat(e.target.value) || 0 })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Cost Override (Optional)" value={editingCustomer?.cost_override ?? ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, cost_override: e.target.value })} helperText="Leave blank to use the plan's default cost" /></Grid>
```

- [ ] **Step 4: Add Estimated Profit + Variance to the Financial Report chart**

Find this exact code in `frontend/src/components/EnhancedReportsView.js`:

```js
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={reportData.monthly_data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="month" />
              <YAxis />
              <Tooltip formatter={(value) => formatCurrency(value)} />
              <Legend />
              <Bar dataKey="income" name="Income" fill="#4ade80" />
              <Bar dataKey="expenses" fill="#f87171" name="Expenses" />
              <Bar dataKey="profit" fill="#60a5fa" name="Profit" />
            </BarChart>
          </ResponsiveContainer>
```

Replace with:

```js
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={reportData.monthly_data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="month" />
              <YAxis />
              <Tooltip formatter={(value) => (value == null ? '—' : formatCurrency(value))} />
              <Legend />
              <Bar dataKey="income" name="Income" fill="#4ade80" />
              <Bar dataKey="expenses" fill="#f87171" name="Expenses" />
              <Bar dataKey="profit" fill="#60a5fa" name="Profit" />
              <Bar dataKey="estimated_profit" fill="#c084fc" name="Estimated Profit" />
            </BarChart>
          </ResponsiveContainer>
```

- [ ] **Step 5: Add an Estimated Profit summary card**

Find this exact code in `frontend/src/components/EnhancedReportsView.js`:

```js
          <Box mt={4} mb={2} display="flex" justifyContent="space-around" flexWrap="wrap">
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#f0fdf4', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="success.main">Total Income</Typography>
               <Typography variant="h5">{formatCurrency(reportData.totals.income)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#fef2f2', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="error.main">Total Expenses</Typography>
               <Typography variant="h5">{formatCurrency(reportData.totals.expenses)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#eff6ff', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="primary.main">Total Profit</Typography>
               <Typography variant="h5" fontWeight="bold">{formatCurrency(reportData.totals.profit)}</Typography>
            </Paper>
          </Box>
```

Replace with:

```js
          <Box mt={4} mb={2} display="flex" justifyContent="space-around" flexWrap="wrap">
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#f0fdf4', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="success.main">Total Income</Typography>
               <Typography variant="h5">{formatCurrency(reportData.totals.income)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#fef2f2', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="error.main">Total Expenses</Typography>
               <Typography variant="h5">{formatCurrency(reportData.totals.expenses)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#eff6ff', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="primary.main">Total Profit</Typography>
               <Typography variant="h5" fontWeight="bold">{formatCurrency(reportData.totals.profit)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#faf5ff', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" sx={{ color: '#a855f7' }}>Estimated Profit</Typography>
               <Typography variant="h5" fontWeight="bold">{formatCurrency(reportData.totals.estimated_profit)}</Typography>
            </Paper>
          </Box>
```

- [ ] **Step 6: Add Estimated Profit + Variance columns to the monthly breakdown table**

Find this exact code in `frontend/src/components/EnhancedReportsView.js`:

```js
          <Typography variant="h6" gutterBottom sx={{ mt: 2 }}>
            Monthly Breakdown
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Month</TableCell>
                  <TableCell align="right">Income</TableCell>
                  <TableCell align="right">Expenses</TableCell>
                  <TableCell align="right">Profit</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {reportData.monthly_data.map((row) => (
                  <TableRow key={row.month}>
                    <TableCell>{row.month}</TableCell>
                    <TableCell align="right" sx={{ color: 'success.main' }}>{formatCurrency(row.income)}</TableCell>
                    <TableCell align="right" sx={{ color: 'error.main' }}>{formatCurrency(row.expenses)}</TableCell>
                    <TableCell align="right" sx={{ color: 'primary.main', fontWeight: 'bold' }}>{formatCurrency(row.profit)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
```

Replace with:

```js
          <Typography variant="h6" gutterBottom sx={{ mt: 2 }}>
            Monthly Breakdown
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Month</TableCell>
                  <TableCell align="right">Income</TableCell>
                  <TableCell align="right">Expenses</TableCell>
                  <TableCell align="right">Profit</TableCell>
                  <TableCell align="right">Estimated Profit</TableCell>
                  <TableCell align="right">Variance</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {reportData.monthly_data.map((row) => (
                  <TableRow key={row.month}>
                    <TableCell>{row.month}</TableCell>
                    <TableCell align="right" sx={{ color: 'success.main' }}>{formatCurrency(row.income)}</TableCell>
                    <TableCell align="right" sx={{ color: 'error.main' }}>{formatCurrency(row.expenses)}</TableCell>
                    <TableCell align="right" sx={{ color: 'primary.main', fontWeight: 'bold' }}>{formatCurrency(row.profit)}</TableCell>
                    <TableCell align="right" sx={{ color: '#a855f7' }}>{row.estimated_profit == null ? '—' : formatCurrency(row.estimated_profit)}</TableCell>
                    <TableCell align="right" sx={{ color: row.variance == null ? 'text.secondary' : (row.variance >= 0 ? 'success.main' : 'error.main'), fontWeight: 'bold' }}>
                      {row.variance == null ? '—' : formatCurrency(row.variance)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
```

- [ ] **Step 7: Start the app and verify manually in the browser**

Start the backend and frontend dev servers, then:
1. Log in as an admin, open Subscription Plans, add/edit a plan — confirm the "Cost" field is present, saves, and reloads correctly.
2. Open Subscriptions, add a customer on that plan — confirm the "Cost Override (Optional)" field appears and can be left blank.
3. Edit that customer and set a Cost Override — confirm it saves; reopen the edit dialog and confirm the value persists.
4. Clear the Cost Override (empty the field, save) — confirm it goes back to blank/using the plan's cost.
5. Open Enhanced Reports → Financial Report — confirm the chart shows a 4th "Estimated Profit" bar, the summary row shows a 4th "Estimated Profit" card, and the table shows "Estimated Profit" and "Variance" columns with correct color coding (green when real profit beats the estimate, red otherwise).
6. Confirm a month with no logged estimate (far in the past, before this feature) renders "—" rather than "$0.00" in both new columns.

Expected: no console errors (`read_console_messages`), no failed network requests (`read_network_requests`), all of the above visible and correct.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/SubscriptionPlanForm.js frontend/src/components/SubscriptionsView.js frontend/src/components/EnhancedReportsView.js
git commit -m "feat(profit-estimation): add plan cost, customer cost override, and report comparison UI"
```

---

## Self-Review Notes

- **Spec coverage:** Hybrid cost model (plan default + customer override) — Task 1/3/4. Yearly amortization and unrecognized-cycle skip — Task 2. Event-driven recompute on add/cancel/reactivate/delete/edit-customer and edit-plan — Tasks 3-4. Daily scheduler safety net for quiet months — Task 5. Frozen-by-time-passing monthly log — Task 1's unique constraint + Task 2's current-month-only upsert (no task ever writes to a past month's row). Financial Report extension with `estimated_profit`/`variance`, `null` for unlogged months, lazy current-month backfill — Task 6. UI: plan cost field, customer cost override field, chart/cards/table additions — Task 7. All confirmed design decisions have at least one test asserting them.
- **Placeholder scan:** no TBD/TODO; every step has runnable code and an exact expected test result.
- **Type consistency:** `MonthlyProfitEstimate.to_dict()`'s keys (`month`, `estimated_income`, `estimated_cost`, `estimated_profit`) are internal/debugging-only — the actual public surface is `GET /api/reports/financial`'s `monthly_data[].estimated_profit`/`variance` and `totals.estimated_profit`/`variance`, used identically by Task 6's tests and Task 7's `EnhancedReportsView.js` edits (`row.estimated_profit`, `row.variance`, `reportData.totals.estimated_profit`). `cost_override` is threaded consistently: `Customer.cost_override` (Task 1) → accepted by `add_customer`/`update_customer` and returned by the customer list + update response (Task 3) → `newCustomer.cost_override`/`editingCustomer.cost_override` in the frontend (Task 7).
