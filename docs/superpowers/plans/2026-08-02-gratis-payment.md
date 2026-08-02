# Gratis (Free) Payments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin/finance user mark an existing, still-unpaid `Payment` as gratis (waived) — settled without charging the customer, excluded from revenue, with the original billed amount preserved and shown with a "GRATIS" badge.

**Architecture:** Two new nullable/boolean columns on the existing `Payment` model (`is_gratis`, `gratis_note`), a new dedicated Flask endpoint (`PUT /api/payments/<id>/mark_gratis`) separate from the existing `mark_payment_as_paid`, an added `is_gratis == False` filter on the four existing revenue-report queries, and a small UI addition to `PaymentsView.js` (button + dialog + badge) mirroring the existing "Mark Paid" flow.

**Tech Stack:** Flask + SQLAlchemy + Alembic (backend, `app.py`), React + MUI (frontend, `frontend/src/components/PaymentsView.js`, `frontend/src/context/AppContext.js`), pytest (backend tests).

## Global Constraints

- Every tenant-scoped read/write on `Payment` must go through `tenant_query(Payment)` — never a bare `Payment.query` — per this codebase's multi-tenancy convention (see `tenancy.py`).
- Marking gratis must NOT modify `customer.balance` or `payment.amount` (spec requirement — money never changed hands, original billed amount is preserved for audit).
- Only `admin` or `finance` roles may mark a payment gratis (403 otherwise) — same gate as the existing "pay" action in `mark_payment_as_paid`.
- An already-`paid` payment (whether via real payment or previously marked gratis) cannot be marked gratis again — 400.
- `Payment.gratis_note` is a column **separate** from the existing `Payment.reason` (which is already populated by the manual "Add Payment" flow) — never overwrite `reason`.
- Do not touch receipt generation (`generate_receipts_for_month`, `GET /api/receipt/<id>`) — explicitly out of scope per spec.
- Do not add bulk "Mark Gratis" — single-payment action only, per spec.
- Local build/tests must pass; do not push to GitHub until the user has tested locally and given the go-ahead (explicit instruction for this feature).

---

## File Structure

- Modify: `app.py` — `Payment` model (add 2 columns), new `mark_payment_gratis` route, `get_payments` serialization, 4 revenue-report queries.
- Create: `migrations/versions/<hash>_add_payment_gratis_fields.py` — Alembic migration for the 2 new columns.
- Create: `tests/test_gratis_payment.py` — backend tests for the new endpoint and report exclusion.
- Modify: `frontend/src/context/AppContext.js` — new `markPaymentGratis` apiService method.
- Modify: `frontend/src/components/PaymentsView.js` — "Mark Gratis" button, dialog, "GRATIS" badge.

---

### Task 1: Data model + `mark_gratis` endpoint (backend core)

**Files:**
- Modify: `app.py` (Payment model ~L424-442, `get_payments` ~L2313-2329, new route after `mark_payment_as_paid` ~L2573)
- Create: `migrations/versions/<hash>_add_payment_gratis_fields.py`
- Create: `tests/test_gratis_payment.py`

**Interfaces:**
- Consumes: `tenant_query`, `current_tenant_id` (from `tenancy.py`, already imported in `app.py`); `tests.conftest.make_tenant` (test helper).
- Produces: `Payment.is_gratis` (bool column), `Payment.gratis_note` (nullable text column); route `PUT /api/payments/<int:payment_id>/mark_gratis` accepting JSON body `{"note": "<optional string>"}`, returning `{"message": ..., "is_gratis": true, "paid": true}` on success. Later tasks (2, 3) depend on these exact names.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gratis_payment.py`:

```python
from tests.conftest import make_tenant


def _make_plan(client, hdr, name="Basic", price=50):
    r = client.post("/api/subscription_plans", headers=hdr,
                    json={"name": name, "price": price, "billing_cycle": "monthly"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["plan"]["id"]


def _make_customer(client, hdr, plan_id, name="Cust"):
    r = client.post("/api/customers", headers=hdr,
                    json={"name": name, "phone": "111", "address": "addr",
                          "subscription_plan_id": plan_id,
                          "subscription_start_date": "2026-01-01"})
    assert r.status_code in (200, 201), r.get_data(as_text=True)
    return r.get_json()["customer_id"]


def _unpaid_payment_id(client, hdr, customer_id):
    payments = client.get("/api/payments", headers=hdr,
                          query_string={"customer_id": customer_id}).get_json()["payments"]
    return next(p["id"] for p in payments if not p["paid"])


def _add_collector(client, admin_hdr, username):
    client.post("/api/users", headers=admin_hdr,
               json={"username": username, "password": "pw", "role": "collector"})
    r = client.post("/api/login", json={"username": username, "password": "pw"})
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


def test_mark_gratis_settles_payment_without_touching_balance(app, client):
    a = make_tenant(client, "Biz A", "a_admin")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    balance_before = client.get(f"/api/customers/{cust_id}/balance", headers=a).get_json()["stored_balance"]

    r = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a,
                   json={"note": "loyalty reward"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["paid"] is True
    assert body["is_gratis"] is True

    balance_after = client.get(f"/api/customers/{cust_id}/balance", headers=a).get_json()["stored_balance"]
    assert balance_after == balance_before  # untouched -- no money collected

    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    updated = next(p for p in payments if p["id"] == payment_id)
    assert updated["is_gratis"] is True
    assert updated["gratis_note"] == "loyalty reward"
    assert updated["amount"] > 0  # original amount preserved, not zeroed


def test_mark_gratis_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_admin2")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    collector_hdr = _add_collector(client, a, "collector1")
    r = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=collector_hdr, json={})
    assert r.status_code == 403


def test_mark_gratis_rejects_already_paid_payment(app, client):
    a = make_tenant(client, "Biz A", "a_admin3")
    plan = _make_plan(client, a)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    ok = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert ok.status_code == 200

    again = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert again.status_code == 400


def test_mark_gratis_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_admin4")
    b = make_tenant(client, "Biz B", "b_admin4")
    plan_b = _make_plan(client, b)
    cust_b = _make_customer(client, b, plan_b)
    payment_id_b = _unpaid_payment_id(client, b, cust_b)

    r = client.put(f"/api/payments/{payment_id_b}/mark_gratis", headers=a, json={})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gratis_payment.py -v`
Expected: FAIL — `PUT /api/payments/<id>/mark_gratis` doesn't exist yet (404 route-not-found on every test), and `get_payments` doesn't return `is_gratis`/`gratis_note` keys.

- [ ] **Step 3: Create the Alembic migration**

Find current head revision:
```bash
grep -L "down_revision = '" migrations/versions/*.py
```
(Confirms `d4f8b2a91c6e` is the current head — no other file has it as a `down_revision`.)

Create `migrations/versions/a1c9e4f2b6d3_add_payment_gratis_fields.py`:

```python
"""add payment gratis fields

Revision ID: a1c9e4f2b6d3
Revises: d4f8b2a91c6e
Create Date: 2026-08-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c9e4f2b6d3'
down_revision = 'd4f8b2a91c6e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_gratis', sa.Boolean(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('gratis_note', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_column('gratis_note')
        batch_op.drop_column('is_gratis')
```

Run it against the local dev DB:
```bash
python -m flask db upgrade
```
Expected: no errors, migration applies cleanly.

- [ ] **Step 4: Add the columns to the `Payment` model**

In `app.py`, the `Payment` class (currently ends at the `received_by`/`collected_by` relationship lines, ~L440-442):

```python
class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey('tenant.id'), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    paid = db.Column(db.Boolean, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    collected = db.Column(db.Boolean, default=False)
    collected_at = db.Column(db.DateTime, nullable=True)
    collected_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    collected_amount = db.Column(db.Float, nullable=True)
    received_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    pre_payment = db.Column(db.Boolean, default=False)
    is_gratis = db.Column(db.Boolean, nullable=False, default=False)
    gratis_note = db.Column(db.Text, nullable=True)
    addon_purchases = db.relationship('AddonPurchase', backref='payment', lazy=True)

    collected_by = db.relationship('User', foreign_keys=[collected_by_id])
    received_by = db.relationship('User', foreign_keys=[received_by_id])
```

- [ ] **Step 5: Add the `mark_gratis` endpoint**

In `app.py`, immediately after `mark_payment_as_paid` (after its closing `except` block, before `@app.route('/api/payments/bulk_mark_paid', ...)` — currently ~L2573-2576):

```python
@app.route('/api/payments/<int:payment_id>/mark_gratis', methods=['PUT'])
@jwt_required()
def mark_payment_gratis(payment_id):
    current_username = get_jwt_identity()
    current_user = User.query.filter_by(username=current_username).first()

    payment = tenant_query(Payment).filter_by(id=payment_id).first()
    if not payment:
        return jsonify({'message': 'Payment not found!'}), 404

    roles = [r.strip().lower() for r in current_user.role.split(',')]
    if 'admin' not in roles and 'finance' not in roles:
        return jsonify({'message': 'Unauthorized. Only finance or admin can mark a payment gratis.'}), 403

    if payment.paid:
        return jsonify({'message': 'Payment is already settled and cannot be marked gratis.'}), 400

    data = request.json or {}
    payment.paid = True
    payment.paid_at = datetime.utcnow()
    payment.is_gratis = True
    payment.gratis_note = data.get('note') or None
    payment.received_by_id = current_user.id
    db.session.commit()

    return jsonify({
        'message': 'Payment marked gratis — no charge recorded.',
        'paid': payment.paid,
        'is_gratis': payment.is_gratis
    })
```

- [ ] **Step 6: Add the new fields to `get_payments` serialization**

In `app.py`, in `get_payments` (~L2313-2329), add two keys to the per-payment dict:

```python
        'payments': [{
            'id': p.id,
            'customer_id': p.customer_id,
            'amount': float(p.amount),
            'paid': p.paid,
            'date': p.date.strftime('%Y-%m-%d'),
            'paid_at': p.paid_at.strftime('%Y-%m-%d %H:%M:%S') if p.paid_at else None,
            'collected': p.collected,
            'collected_at': p.collected_at.strftime('%Y-%m-%d %H:%M:%S') if p.collected_at else None,
            'collected_amount': float(p.collected_amount) if p.collected_amount is not None else None,
            'collected_by': p.collected_by.username if p.collected_by else None,
            'received_by': p.received_by.username if p.received_by else None,
            'pre_payment': p.pre_payment,
            'reason': p.reason,
            'is_gratis': p.is_gratis,
            'gratis_note': p.gratis_note,
            'customer_name': p.customer.name,
            'customer_address': p.customer.address
             } for p in pagination.items],
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/test_gratis_payment.py -v`
Expected: all 4 tests PASS.

Also run the full suite to check nothing else broke:
Run: `python -m pytest -q`
Expected: all tests pass (previous count plus 4 new ones).

- [ ] **Step 8: Commit**

```bash
git add app.py migrations/versions/a1c9e4f2b6d3_add_payment_gratis_fields.py tests/test_gratis_payment.py
git commit -m "feat: add mark-payment-gratis endpoint and data model

New Payment.is_gratis/gratis_note columns and PUT
/api/payments/<id>/mark_gratis let admin/finance settle a payment as
waived without charging the customer or touching their balance, while
preserving the original billed amount for the audit trail."
```

---

### Task 2: Exclude gratis payments from revenue reports

**Files:**
- Modify: `app.py` (`get_total_sales` ~L2382-2397, `get_monthly_revenue` sales query ~L3060-3071, `get_revenue_report` ~L4522-4530, financial report income query ~L5169-5178)
- Modify: `tests/test_gratis_payment.py`

**Interfaces:**
- Consumes: `Payment.is_gratis` (from Task 1).
- Produces: nothing new consumed by later tasks — this task only changes query filters.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gratis_payment.py`:

```python
def test_gratis_payment_excluded_from_revenue_reports(app, client):
    a = make_tenant(client, "Biz A", "a_admin5")
    plan = _make_plan(client, a, price=75)
    cust_id = _make_customer(client, a, plan)
    payment_id = _unpaid_payment_id(client, a, cust_id)

    r = client.put(f"/api/payments/{payment_id}/mark_gratis", headers=a, json={})
    assert r.status_code == 200

    total_sales = client.get("/api/reports/total-sales", headers=a).get_json()
    assert sum(row["value"] for row in total_sales) == 0

    # No expenses/supplier/salary payments recorded in this fresh tenant, so
    # monthly-revenue's net `value` (sales - expenses) directly reflects sales.
    monthly_revenue = client.get("/api/reports/monthly-revenue", headers=a).get_json()
    assert sum(row["value"] for row in monthly_revenue) == 0

    revenue_detail = client.get("/api/reports/revenue", headers=a).get_json()
    assert revenue_detail["total_revenue"] == 0
    assert revenue_detail["payment_count"] == 0

    financial = client.get("/api/reports/financial", headers=a,
                           query_string={"start_date": "2026-01-01T00:00:00Z",
                                         "end_date": "2026-12-31T00:00:00Z"}).get_json()
    assert sum(row["income"] for row in financial["monthly_data"]) == 0

    # Still visible in the plain payments list (not hidden, just not revenue).
    payments = client.get("/api/payments", headers=a,
                          query_string={"customer_id": cust_id}).get_json()["payments"]
    assert any(p["id"] == payment_id and p["is_gratis"] for p in payments)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gratis_payment.py::test_gratis_payment_excluded_from_revenue_reports -v`
Expected: FAIL — the gratis payment's amount is currently still summed as revenue (no `is_gratis` filter yet).

- [ ] **Step 3: Add the `is_gratis` filter to the four revenue queries**

In `app.py`:

`get_total_sales` (~L2385-2392):
```python
    total_sales = db.session.query(
        month_key(func.coalesce(Payment.paid_at, Payment.date)).label('month'),
        func.sum(Payment.amount).label('total_sales')
    ).filter(
        Payment.tenant_id == current_tenant_id(),
        Payment.paid == True,
        Payment.is_gratis == False,
        Payment.pre_payment == False
    ).group_by('month').all()
```

`get_monthly_revenue` sales query (~L3064-3071):
```python
    sales_query = db.session.query(
        month_key(func.coalesce(Payment.paid_at, Payment.date)).label('month'),
        func.sum(Payment.amount).label('total_sales')
    ).filter(
        Payment.tenant_id == current_tenant_id(),
        Payment.paid == True,
        Payment.is_gratis == False,
        Payment.pre_payment == False
    ).group_by('month').all()
```

`get_revenue_report` (~L4528):
```python
    query = tenant_query(Payment).filter(Payment.paid == True, Payment.is_gratis == False).options(
        db.joinedload(Payment.customer).joinedload(Customer.subscription_plan)
    )
```

Financial report income query (~L5170-5178):
```python
        income_query = db.session.query(
            month_key(func.coalesce(Payment.paid_at, Payment.date)).label('month'),
            func.sum(Payment.amount).label('total')
        ).filter(
            Payment.tenant_id == current_tenant_id(),
            Payment.paid == True,
            Payment.is_gratis == False,
            func.coalesce(Payment.paid_at, Payment.date) >= start_date,
            func.coalesce(Payment.paid_at, Payment.date) <= end_date
        ).group_by('month').all()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gratis_payment.py -v`
Expected: all 5 tests PASS.

Run the full suite:
Run: `python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_gratis_payment.py
git commit -m "fix: exclude gratis payments from revenue reports

total-sales, monthly-revenue, revenue, and financial income queries now
filter out is_gratis payments, since no money was actually collected."
```

---

### Task 3: Frontend — "Mark Gratis" button, dialog, and badge

**Files:**
- Modify: `frontend/src/context/AppContext.js` (~L226, next to `markPaymentAsPaid`)
- Modify: `frontend/src/components/PaymentsView.js` (state ~L400-404, handlers after `handleMarkPaid` ~L707, table row ~L1367-1390, dialogs ~L1489-1519)

**Interfaces:**
- Consumes: `PUT /api/payments/<id>/mark_gratis` (Task 1), `payment.is_gratis`/`payment.gratis_note` fields (Task 1).
- Produces: `apiService.markPaymentGratis(paymentId, note)` — used only within this task.

- [ ] **Step 1: Add the apiService method**

In `frontend/src/context/AppContext.js`, find:
```js
    markPaymentAsPaid: (paymentId, data = {}) => api.put(`/payments/${paymentId}/mark_paid`, data),
```
Add immediately after it:
```js
    markPaymentGratis: (paymentId, note) => api.put(`/payments/${paymentId}/mark_gratis`, { note }),
```

(If the exact line above isn't found verbatim, search for `mark_paid` in the same file and add the new line next to it — same object, same style.)

- [ ] **Step 2: Add dialog state and handlers**

In `frontend/src/components/PaymentsView.js`, after the existing mark-paid dialog state (~L402-403):

```js
    // Mark-as-Gratis dialog
    const [markGratisDialog, setMarkGratisDialog] = useState({ open: false, paymentId: null, outstanding: 0, customerName: '' });
    const [markGratisNote, setMarkGratisNote] = useState('');
    const [markGratisSubmitting, setMarkGratisSubmitting] = useState(false);
```

After `handleMarkPaid` (immediately after its closing `};`, ~L707):

```js
    const openMarkGratisDialog = (payment) => {
        setMarkGratisDialog({ open: true, paymentId: payment.id, outstanding: payment.amount, customerName: payment.customer_name });
        setMarkGratisNote('');
    };

    const handleMarkGratis = async () => {
        if (markGratisSubmitting) return;
        const { paymentId } = markGratisDialog;
        setMarkGratisSubmitting(true);
        setMarkGratisDialog({ open: false, paymentId: null, outstanding: 0, customerName: '' });
        try {
            const response = await apiService.markPaymentGratis(paymentId, markGratisNote);
            setSnackbar({ open: true, message: response.data.message, severity: 'success' });
            fetchPayments();
            if (filters.customer_id) {
                fetchCustomerBalance(filters.customer_id);
            }
        } catch (error) {
            console.error("Error marking payment gratis:", error);
            setSnackbar({ open: true, message: 'Failed to mark payment gratis. ' + (error.response?.data?.message || error.message), severity: 'error' });
        } finally {
            setMarkGratisSubmitting(false);
        }
    };
```

- [ ] **Step 3: Add the "Mark Gratis" button and "GRATIS" badge to the table row**

In `frontend/src/components/PaymentsView.js`, the status chip (~L1372-1376) becomes:

```jsx
                                            <TableCell>
                                                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                                                    <Chip label={payment.paid ? 'Paid' : 'Unpaid'} size="small"
                                                        sx={{ bgcolor: alpha(getStatusColor(payment.paid), 0.1), color: getStatusColor(payment.paid), fontWeight: 600, border: `1px solid ${alpha(getStatusColor(payment.paid), 0.25)}` }}
                                                    />
                                                    {payment.is_gratis && (
                                                        <Chip label="GRATIS" size="small" title={payment.gratis_note || ''}
                                                            sx={{ bgcolor: alpha(theme.palette.info.main, 0.1), color: theme.palette.info.main, fontWeight: 700, border: `1px solid ${alpha(theme.palette.info.main, 0.25)}` }}
                                                        />
                                                    )}
                                                </Box>
                                            </TableCell>
```

And the actions cell (~L1382-1389), the existing "Collect"/"Confirm Receipt" button gains a sibling, both still gated on `!payment.paid`:

```jsx
                                            <TableCell sx={{ whiteSpace: 'nowrap' }}>
                                                {!payment.paid && (
                                                    <Tooltip title={payment.collected ? "Confirm Receipt" : "Collect"}>
                                                        <IconButton size="small" color="success" onClick={() => openMarkPaidDialog(payment)}>
                                                            <CheckCircleIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
                                                {!payment.paid && (userRoles.includes('admin') || userRoles.includes('finance')) && (
                                                    <Tooltip title="Mark Gratis (Free)">
                                                        <IconButton size="small" color="info" onClick={() => openMarkGratisDialog(payment)}>
                                                            <CardGiftcardIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
```

Add the icon to the existing destructured import from `@mui/icons-material` (~L43-57), which uses the `{ IconName as AliasIcon }` pattern — add one entry to that same block:
```js
    CardGiftcard as CardGiftcardIcon,
```

- [ ] **Step 4: Add the "Mark Gratis" dialog**

In `frontend/src/components/PaymentsView.js`, immediately after the existing `markPaidDialog` `<Dialog>` block closes (~L1519, right after `</Dialog>`):

```jsx
            <Dialog open={markGratisDialog.open} onClose={() => setMarkGratisDialog({ open: false, paymentId: null, outstanding: 0, customerName: '' })} maxWidth="xs" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Mark Payment Gratis</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" sx={{ mb: 2, color: 'text.secondary' }}>
                        Customer: <strong>{markGratisDialog.customerName}</strong><br />
                        Original amount: <strong>${(markGratisDialog.outstanding || 0).toFixed(2)}</strong><br />
                        This waives the payment — no charge will be recorded and the customer's balance will not change.
                    </Typography>
                    <TextField
                        fullWidth
                        autoFocus
                        label="Reason (optional)"
                        value={markGratisNote}
                        onChange={(e) => setMarkGratisNote(e.target.value)}
                        placeholder="e.g. loyalty reward, outage compensation"
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setMarkGratisDialog({ open: false, paymentId: null, outstanding: 0, customerName: '' })}>Cancel</Button>
                    <Button
                        variant="contained"
                        color="info"
                        startIcon={markGratisSubmitting ? <CircularProgress size={16} color="inherit" /> : <CardGiftcardIcon />}
                        onClick={handleMarkGratis}
                        disabled={markGratisSubmitting}
                    >
                        Confirm Gratis
                    </Button>
                </DialogActions>
            </Dialog>
```

- [ ] **Step 5: Build the frontend to verify it compiles**

Run: `cd frontend && CI=true npx react-scripts build`
Expected: build succeeds (only pre-existing lint warnings, no new errors).

- [ ] **Step 6: Manual local verification**

Run the backend locally (`python app.py` or the project's usual local-dev command) and the frontend dev server (`npm start` in `frontend/`), then:
1. Log in as an admin.
2. Go to Payments, find an unpaid payment.
3. Confirm both "Collect"/"Confirm Receipt" and the new gift-icon "Mark Gratis" buttons appear.
4. Click "Mark Gratis", optionally type a reason, confirm.
5. Verify: the row now shows "Paid" + a "GRATIS" chip, the amount is unchanged (not zeroed), and the customer's balance (Customers view) did not increase.
6. Log in as a `collector`-only user and confirm the "Mark Gratis" button does not appear.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/context/AppContext.js frontend/src/components/PaymentsView.js
git commit -m "feat: add Mark Gratis button, dialog, and badge to Payments view

Lets admin/finance settle an unpaid payment as gratis (waived) from the
payments table, with an optional reason note and a GRATIS badge on
settled rows."
```

**Do not push these commits to GitHub until the user has completed Step 6 (manual local verification) and confirmed it's working.**
