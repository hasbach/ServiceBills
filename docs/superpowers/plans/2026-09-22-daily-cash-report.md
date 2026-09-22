# Daily Cash Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily cash-reconciliation report — for a chosen calendar day, how much cash each field collector collected and how much was paid directly at the office, so it can be checked against what's physically handed in.

**Architecture:** One new read-only backend endpoint (`GET /api/reports/daily-cash`) that queries existing `Payment` rows (no new tables), grouped in Python by collector; one new option in `EnhancedReportsView.js`'s existing report-type dropdown, reusing its existing fetch/render scaffolding.

**Tech Stack:** Flask + Flask-SQLAlchemy (`app.py`), pytest (`tests/`); React 18 (CRA) + MUI (`frontend/src/components/EnhancedReportsView.js`), Jest.

**Spec:** `docs/superpowers/specs/2026-09-22-daily-cash-report-design.md`

## Global Constraints

- **Cash = `Payment.collected_via IS NULL`.** No new `payment_method` field — every non-Whish paid payment counts as cash (spec Non-goals).
- **No new database tables/models.** Live query only.
- **Day boundary is decided by the frontend, in the browser's local time**, and sent to the backend as exact ISO instants via `start_date`/`end_date` query params. The backend does no timezone math and must NOT reinterpret/override the times it's given (unlike `get_collector_progress`, which forces `end_date` to `23:59:59` — this endpoint must not do that).
- **Auth: `admin` or `finance` role only**, using the exact role-check pattern at `app.py:4963-4965` (`mark_payment_gratis`).
- **`Payment.fx_rate_to_reporting` is `NOT NULL`, default `1`** (`app.py:1110`) — there is no "missing rate" case; every payment converts as `amount * float(fx_rate_to_reporting)`.
- **Attribution**: a payment's group key is `collected_by_id` if set, else `received_by_id`'s bucket is labeled `"Office / Direct"` (`collector_id: null`). Never both.
- Frontend tests **must** use an explicit pattern — the plain CRA command silently reports "No tests found" in this checkout:
  `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/<file>.test.js"`
- Backend tests: `python -m pytest tests/<file>.py -q -p no:warnings`
- Build check is `cd frontend && npx react-scripts build` **without** `CI=true` (matches the Dockerfile; `CI=true` fails on ~30 pre-existing warnings in unrelated files).
- Never commit anything under `frontend/build/` or `build/`.
- Do not use `git stash` — the stash stack is shared across worktrees. Use a WIP commit if you need to switch away mid-task.

---

## File Structure

- Create: `tests/test_daily_cash_report.py` — backend endpoint tests.
- Modify: `app.py` — new `/api/reports/daily-cash` route, inserted immediately after `get_collector_progress` (ends `app.py:8831`) and before `get_financial_report` (`app.py:8833`).
- Create: `frontend/src/components/dailyCashDateRange.js` — pure function computing the local-day ISO boundary, mirroring `formatStamp.js`'s "small pure timezone-aware helper" pattern.
- Create: `frontend/src/components/dailyCashDateRange.test.js`
- Modify: `frontend/src/components/EnhancedReportsView.js` — new dropdown option, conditional date picker, fetch branch, render function.

---

### Task 1: Backend `/api/reports/daily-cash` endpoint

**Files:**
- Create: `tests/test_daily_cash_report.py`
- Modify: `app.py:8831-8833` (insert new route between `get_collector_progress` and `get_financial_report`)

**Interfaces:**
- Produces: `GET /api/reports/daily-cash?start_date=<ISO>&end_date=<ISO>` → `200 {date_start, date_end, grand_total, reporting_currency, groups: [{collector_id, collector_name, is_office, total, payment_count, payments: [{id, customer_name, amount, currency, reporting_amount, time}]}]}`, `400` if params missing, `403` if caller isn't admin/finance.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_daily_cash_report.py`:

```python
from datetime import datetime, timedelta

from tests.conftest import make_tenant
from app import app as flask_app, db, Payment, Customer


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


def _day_range(day_str):
    """start_date/end_date for one UTC calendar day, matching what the
    frontend's localDayRange() sends for a browser in the UTC zone."""
    start = f"{day_str}T00:00:00.000Z"
    end_dt = datetime.strptime(day_str, "%Y-%m-%d") + timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%dT00:00:00.000Z")
    return start, end


def _today_range():
    return _day_range(datetime.utcnow().strftime("%Y-%m-%d"))


def test_daily_cash_groups_by_field_collector_and_totals_correctly(app, client):
    a = make_tenant(client, "Biz A", "a_cash1")
    plan = _make_plan(client, a, price=80)
    cust1 = _make_customer(client, a, plan, name="Cust1")
    cust2 = _make_customer(client, a, plan, name="Cust2")
    pay1 = _unpaid_payment_id(client, a, cust1)
    pay2 = _unpaid_payment_id(client, a, cust2)

    collector1 = _add_collector(client, a, "collector1_cash1")
    collector2 = _add_collector(client, a, "collector2_cash1")

    client.put(f"/api/payments/{pay1}/mark_paid", headers=collector1, json={"action": "collect"})
    client.put(f"/api/payments/{pay1}/mark_paid", headers=a, json={"action": "pay"})
    client.put(f"/api/payments/{pay2}/mark_paid", headers=collector2, json={"action": "collect"})
    client.put(f"/api/payments/{pay2}/mark_paid", headers=a, json={"action": "pay"})

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["grand_total"] == 160
    totals_by_name = {g["collector_name"]: g["total"] for g in body["groups"]}
    assert totals_by_name["collector1_cash1"] == 80
    assert totals_by_name["collector2_cash1"] == 80
    collector_group = next(g for g in body["groups"] if g["collector_name"] == "collector1_cash1")
    assert collector_group["is_office"] is False
    assert collector_group["payment_count"] == 1
    assert collector_group["payments"][0]["customer_name"] == "Cust1"


def test_daily_cash_office_direct_bucket_when_no_field_collector(app, client):
    a = make_tenant(client, "Biz A", "a_cash2")
    plan = _make_plan(client, a, price=45)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)

    client.put(f"/api/payments/{pay}/mark_paid", headers=a, json={"action": "pay"})

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    body = r.get_json()
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert group["is_office"] is True
    assert group["collector_id"] is None
    assert group["collector_name"] == "Office / Direct"
    assert group["total"] == 45


def test_daily_cash_excludes_whish_refund_gratis_and_reverted_payments(app, client):
    a = make_tenant(client, "Biz A", "a_cash3")
    plan = _make_plan(client, a, price=30)
    cust = _make_customer(client, a, plan)

    with flask_app.app_context():
        tenant_id = Customer.query.filter_by(id=cust).first().tenant_id
        now = datetime.utcnow()
        rows = [
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    collected_via='whish'),
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    is_refund=True),
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    is_gratis=True),
            Payment(tenant_id=tenant_id, customer_id=cust, amount=30, currency='USD',
                    fx_rate_to_reporting=1, paid=True, paid_at=now, collected_at=now,
                    reverted_at=now),
        ]
        db.session.add_all(rows)
        db.session.commit()

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    body = r.get_json()
    assert body["grand_total"] == 0
    assert body["groups"] == []


def test_daily_cash_respects_the_day_boundary(app, client):
    a = make_tenant(client, "Biz A", "a_cash4")
    plan = _make_plan(client, a, price=20)
    cust = _make_customer(client, a, plan)
    pay = _unpaid_payment_id(client, a, cust)
    client.put(f"/api/payments/{pay}/mark_paid", headers=a, json={"action": "pay"})

    with flask_app.app_context():
        p = Payment.query.get(pay)
        p.collected_at = datetime(2026, 6, 15, 12, 0, 0)
        p.paid_at = datetime(2026, 6, 15, 12, 0, 0)
        db.session.commit()

    start, end = _day_range("2026-06-15")
    same_day = client.get("/api/reports/daily-cash", headers=a,
                          query_string={"start_date": start, "end_date": end}).get_json()
    assert same_day["grand_total"] == 20

    start_prev, end_prev = _day_range("2026-06-14")
    prev_day = client.get("/api/reports/daily-cash", headers=a,
                          query_string={"start_date": start_prev, "end_date": end_prev}).get_json()
    assert prev_day["grand_total"] == 0

    start_next, end_next = _day_range("2026-06-16")
    next_day = client.get("/api/reports/daily-cash", headers=a,
                          query_string={"start_date": start_next, "end_date": end_next}).get_json()
    assert next_day["grand_total"] == 0


def test_daily_cash_rejects_non_admin_finance(app, client):
    a = make_tenant(client, "Biz A", "a_cash5")
    collector_hdr = _add_collector(client, a, "collector_cash5")
    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=collector_hdr,
                    query_string={"start_date": start, "end_date": end})
    assert r.status_code == 403


def test_daily_cash_is_tenant_scoped(app, client):
    a = make_tenant(client, "Biz A", "a_cash6")
    b = make_tenant(client, "Biz B", "b_cash6")
    plan_b = _make_plan(client, b)
    cust_b = _make_customer(client, b, plan_b)
    pay_b = _unpaid_payment_id(client, b, cust_b)
    client.put(f"/api/payments/{pay_b}/mark_paid", headers=b, json={"action": "pay"})

    start, end = _today_range()
    r = client.get("/api/reports/daily-cash", headers=a,
                    query_string={"start_date": start, "end_date": end})
    assert r.get_json()["grand_total"] == 0


def test_daily_cash_requires_start_and_end_date(app, client):
    a = make_tenant(client, "Biz A", "a_cash7")
    r = client.get("/api/reports/daily-cash", headers=a, query_string={"start_date": "2026-06-15T00:00:00.000Z"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_daily_cash_report.py -q -p no:warnings`
Expected: every test fails (404, since the route doesn't exist yet — Flask's default 404 response is not JSON in a way `get_json()` can always parse, so failures may show as `AttributeError`/`assert 404 == 200` rather than a clean assertion; either way, all 7 should fail, none should error with an import problem).

- [ ] **Step 3: Implement the endpoint**

In `app.py`, insert immediately after line 8831 (the `except Exception as e:` block closing `get_collector_progress`) and before line 8833 (`@app.route('/api/reports/financial'...)`):

```python
@app.route('/api/reports/daily-cash', methods=['GET'])
@jwt_required()
def get_daily_cash_report():
    """Cash reconciliation for one calendar day: how much cash each field
    collector collected, plus an 'Office / Direct' bucket for payments with
    no field collector. See docs/superpowers/specs/2026-09-22-daily-cash-report-design.md.
    """
    try:
        current_username = get_jwt_identity()
        current_user = User.query.filter_by(username=current_username).first()
        roles = [r.strip().lower() for r in current_user.role.split(',')]
        if 'admin' not in roles and 'finance' not in roles:
            return jsonify({'message': 'Unauthorized. Only finance or admin can view the daily cash report.'}), 403

        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        if not start_date_str or not end_date_str:
            return jsonify({'error': 'start_date and end_date are required'}), 400

        # The caller (the frontend's localDayRange()) already computed the
        # exact local-day boundary -- unlike get_collector_progress, this
        # endpoint must NOT reinterpret/override the time component.
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(tzinfo=None)

        payments = tenant_query(Payment).filter(
            Payment.paid == True,
            Payment.collected_via.is_(None),   # cash: everything that isn't Whish
            Payment.is_gratis == False,
            Payment.is_refund == False,
            Payment.reverted_at.is_(None),
            func.coalesce(Payment.collected_at, Payment.paid_at) >= start_date,
            func.coalesce(Payment.collected_at, Payment.paid_at) < end_date,
        ).options(
            db.joinedload(Payment.customer),
            db.joinedload(Payment.collected_by),
        ).all()

        groups = {}
        grand_total = 0.0
        for p in payments:
            # fx_rate_to_reporting is NOT NULL (default 1) -- see Global
            # Constraints, no missing-rate case to handle.
            reporting_amount = p.amount * float(p.fx_rate_to_reporting)

            if p.collected_by_id:
                key = p.collected_by_id
                name = p.collected_by.username if p.collected_by else 'Unknown'
                is_office = False
            else:
                key = 'office'
                name = 'Office / Direct'
                is_office = True

            group = groups.setdefault(key, {
                'collector_id': p.collected_by_id,
                'collector_name': name,
                'is_office': is_office,
                'total': 0.0,
                'payment_count': 0,
                'payments': [],
            })
            when = p.collected_at or p.paid_at
            group['total'] += reporting_amount
            group['payment_count'] += 1
            group['payments'].append({
                'id': p.id,
                'customer_name': p.customer.name if p.customer else None,
                'amount': p.amount,
                'currency': p.currency,
                'reporting_amount': reporting_amount,
                'time': when.strftime('%Y-%m-%d %H:%M:%S') if when else None,
            })
            grand_total += reporting_amount

        # Highest-collecting field collector first; Office / Direct always last,
        # regardless of its own total -- it's a different kind of bucket, not
        # another collector to rank (spec: Frontend section).
        group_list = sorted(groups.values(), key=lambda g: (g['is_office'], -g['total']))

        settings = tenant_query(BusinessSettings).first()
        reporting_currency = settings.reporting_currency if settings else 'USD'

        return jsonify({
            'date_start': start_date_str,
            'date_end': end_date_str,
            'grand_total': grand_total,
            'reporting_currency': reporting_currency,
            'groups': group_list,
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_daily_cash_report.py -q -p no:warnings`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_daily_cash_report.py app.py
git commit -m "$(cat <<'EOF'
Add GET /api/reports/daily-cash cash-reconciliation report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Frontend local-day boundary helper

**Files:**
- Create: `frontend/src/components/dailyCashDateRange.js`
- Create: `frontend/src/components/dailyCashDateRange.test.js`

**Interfaces:**
- Produces: `localDayRange(date: Date) -> { startIso: string, endIso: string }` — `startIso` is the ISO instant of local midnight on `date`'s calendar day, `endIso` is local midnight of the following day. Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/dailyCashDateRange.test.js`:

```js
import { localDayRange } from './dailyCashDateRange';

test('returns local midnight of the given day through local midnight of the next day', () => {
  const { startIso, endIso } = localDayRange(new Date(2026, 8, 22, 14, 3, 0));
  expect(new Date(startIso)).toEqual(new Date(2026, 8, 22, 0, 0, 0, 0));
  expect(new Date(endIso)).toEqual(new Date(2026, 8, 23, 0, 0, 0, 0));
});

test('a date already at local midnight is its own start', () => {
  const { startIso } = localDayRange(new Date(2026, 8, 22, 0, 0, 0, 0));
  expect(new Date(startIso)).toEqual(new Date(2026, 8, 22, 0, 0, 0, 0));
});

test('a date just before local midnight stays in that day, not the next one', () => {
  const { startIso, endIso } = localDayRange(new Date(2026, 8, 22, 23, 59, 59, 999));
  expect(new Date(startIso)).toEqual(new Date(2026, 8, 22, 0, 0, 0, 0));
  expect(new Date(endIso)).toEqual(new Date(2026, 8, 23, 0, 0, 0, 0));
});

test('crosses a month boundary correctly', () => {
  const { endIso } = localDayRange(new Date(2026, 8, 30, 10, 0, 0));
  expect(new Date(endIso)).toEqual(new Date(2026, 9, 1, 0, 0, 0, 0));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/dailyCashDateRange.test.js"`
Expected: FAIL — `Cannot find module './dailyCashDateRange'`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/components/dailyCashDateRange.js`:

```js
/**
 * The Daily Cash report reconciles one calendar day in the viewer's own
 * local time, not UTC -- see
 * docs/superpowers/specs/2026-09-22-daily-cash-report-design.md. Given a
 * Date anywhere within a calendar day, returns that day's local-midnight
 * start instant and the following day's local-midnight instant, as ISO
 * strings ready for the API's start_date/end_date params. The backend does
 * no timezone math of its own -- this is where "today" actually gets
 * decided, mirroring how formatStamp.js already trusts the browser's local
 * zone instead of a hardcoded offset.
 */
export function localDayRange(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { startIso: start.toISOString(), endIso: end.toISOString() };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && CI=true npx react-scripts test --watchAll=false --testMatch "**/dailyCashDateRange.test.js"`
Expected: `Tests: 4 passed, 4 total`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/dailyCashDateRange.js frontend/src/components/dailyCashDateRange.test.js
git commit -m "$(cat <<'EOF'
Add localDayRange() helper for the daily cash report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Wire the Daily Cash Report into `EnhancedReportsView.js`

**Files:**
- Modify: `frontend/src/components/EnhancedReportsView.js`

**Interfaces:**
- Consumes: `localDayRange(date) -> { startIso, endIso }` (Task 2). Backend response shape from Task 1 (`{date_start, date_end, grand_total, reporting_currency, groups: [{collector_id, collector_name, is_office, total, payment_count, payments: [{id, customer_name, amount, currency, reporting_amount, time}]}]}`).

- [ ] **Step 1: Add imports**

At the top of `frontend/src/components/EnhancedReportsView.js`, add `Collapse` and `IconButton` to the existing `@mui/material` import block (`EnhancedReportsView.js:2-23`), and add two new imports after it:

```js
import {
  Box,
  Container,
  Typography,
  Paper,
  Grid,
  TextField,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Card,
  CardContent,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Alert,
  Collapse,
  IconButton,
} from '@mui/material';
import { KeyboardArrowDown as KeyboardArrowDownIcon, KeyboardArrowUp as KeyboardArrowUpIcon } from '@mui/icons-material';
import { localDayRange } from './dailyCashDateRange';
```

- [ ] **Step 2: Add expanded-row state**

Immediately after the existing state declarations (`EnhancedReportsView.js:43-53`, ending with `const [customerMetrics, setCustomerMetrics] = useState(null);`), add:

```js
  const [expandedCashGroups, setExpandedCashGroups] = useState({});
```

- [ ] **Step 3: Add the `daily-cash` fetch branch**

In `fetchReportData` (`EnhancedReportsView.js:61-85`), add a new branch before the existing `if (reportType === 'financial')` block:

```js
  const fetchReportData = async () => {
    setReportData(null);
    setReportError(null);
    try {
      if (reportType === 'daily-cash') {
        const { startIso, endIso } = localDayRange(startDate);
        const token = localStorage.getItem('token');
        const response = await fetch(`/api/reports/daily-cash?start_date=${startIso}&end_date=${endIso}`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data?.error || data?.message || `Failed to load report (HTTP ${response.status})`);
        }
        setReportData(data);
        return;
      }

      if (reportType === 'financial') {
        const res = await apiService.fetchFinancialReport(startDate.toISOString(), endDate.toISOString());
        setReportData(res.data);
        return;
      }

      // Fallback for other reports
      const token = localStorage.getItem('token');
      const response = await fetch(`/api/reports/${reportType}?start_date=${startDate.toISOString()}&end_date=${endDate.toISOString()}`, {
         headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.error || data?.message || `Failed to load report (HTTP ${response.status})`);
      }
      setReportData(data);
    } catch (error) {
      console.error('Error fetching report data:', error);
      setReportError(error.message || 'Failed to load report data.');
    }
  };
```

- [ ] **Step 4: Add the render function**

After `renderCustomerMetrics` (`EnhancedReportsView.js:314-357`) and before the `return (` that starts the component's JSX (`EnhancedReportsView.js:359`), add:

```js
  const toggleCashGroupExpanded = (key) => {
    setExpandedCashGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const renderDailyCashReport = () => {
    if (!reportData || reportType !== 'daily-cash' || !Array.isArray(reportData.groups)) return null;

    return (
      <Grid item xs={12}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Daily Cash Report
          </Typography>
          <Typography variant="h5" sx={{ mb: 2 }}>
            Grand Total: {reportData.grand_total.toFixed(2)} {reportData.reporting_currency}
          </Typography>
          {reportData.groups.length === 0 ? (
            <Typography color="text.secondary">No cash payments collected on this day.</Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell />
                    <TableCell>Collector</TableCell>
                    <TableCell align="right">Payments</TableCell>
                    <TableCell align="right">Total</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {reportData.groups.map((group) => {
                    const key = group.is_office ? 'office' : group.collector_id;
                    const isExpanded = !!expandedCashGroups[key];
                    return (
                      <React.Fragment key={key}>
                        <TableRow>
                          <TableCell>
                            <IconButton size="small" onClick={() => toggleCashGroupExpanded(key)}>
                              {isExpanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                            </IconButton>
                          </TableCell>
                          <TableCell>{group.collector_name}</TableCell>
                          <TableCell align="right">{group.payment_count}</TableCell>
                          <TableCell align="right">{group.total.toFixed(2)} {reportData.reporting_currency}</TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell colSpan={4} sx={{ py: 0, border: 0 }}>
                            <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                              <Table size="small">
                                <TableHead>
                                  <TableRow>
                                    <TableCell>Customer</TableCell>
                                    <TableCell align="right">Amount</TableCell>
                                    <TableCell>Currency</TableCell>
                                    <TableCell align="right">Reporting Amount</TableCell>
                                    <TableCell>Time</TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {group.payments.map((p) => (
                                    <TableRow key={p.id}>
                                      <TableCell>{p.customer_name}</TableCell>
                                      <TableCell align="right">{p.amount}</TableCell>
                                      <TableCell>{p.currency}</TableCell>
                                      <TableCell align="right">{p.reporting_amount.toFixed(2)}</TableCell>
                                      <TableCell>{p.time}</TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
      </Grid>
    );
  };
```

- [ ] **Step 5: Add the dropdown option and make the date pickers report-type-aware**

In the report-type `Select` (`EnhancedReportsView.js:369-379`), replace:

```js
                  <Select
                    value={reportType}
                    onChange={(e) => setReportType(e.target.value)}
                  >
                    <MenuItem value="financial">Financial Report</MenuItem>
                    <MenuItem value="revenue">Revenue Report</MenuItem>
                    <MenuItem value="customers">Customer Report</MenuItem>
                    <MenuItem value="payments">Payment Report</MenuItem>
                    <MenuItem value="collector-progress">Collector Progress Report</MenuItem>
                    <MenuItem value="customer-whish-payments">Customer Whish Payments Report</MenuItem>
                  </Select>
```

with:

```js
                  <Select
                    value={reportType}
                    onChange={(e) => {
                      const newType = e.target.value;
                      setReportType(newType);
                      if (newType === 'daily-cash') {
                        setStartDate(new Date());
                      }
                    }}
                  >
                    <MenuItem value="financial">Financial Report</MenuItem>
                    <MenuItem value="revenue">Revenue Report</MenuItem>
                    <MenuItem value="customers">Customer Report</MenuItem>
                    <MenuItem value="payments">Payment Report</MenuItem>
                    <MenuItem value="collector-progress">Collector Progress Report</MenuItem>
                    <MenuItem value="customer-whish-payments">Customer Whish Payments Report</MenuItem>
                    <MenuItem value="daily-cash">Daily Cash Report</MenuItem>
                  </Select>
```

Then, immediately after (`EnhancedReportsView.js:382-401`), replace the Start Date / End Date picker `Grid` items:

```js
              <Grid item xs={12} md={3}>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                  <DatePicker
                    label="Start Date"
                    value={startDate}
                    onChange={setStartDate}
                    renderInput={(params) => <TextField {...params} fullWidth />}
                  />
                </LocalizationProvider>
              </Grid>
              <Grid item xs={12} md={3}>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                  <DatePicker
                    label="End Date"
                    value={endDate}
                    onChange={setEndDate}
                    renderInput={(params) => <TextField {...params} fullWidth />}
                  />
                </LocalizationProvider>
              </Grid>
```

with:

```js
              <Grid item xs={12} md={3}>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                  <DatePicker
                    label={reportType === 'daily-cash' ? 'Date' : 'Start Date'}
                    value={startDate}
                    onChange={setStartDate}
                    renderInput={(params) => <TextField {...params} fullWidth />}
                  />
                </LocalizationProvider>
              </Grid>
              {reportType !== 'daily-cash' && (
                <Grid item xs={12} md={3}>
                  <LocalizationProvider dateAdapter={AdapterDateFns}>
                    <DatePicker
                      label="End Date"
                      value={endDate}
                      onChange={setEndDate}
                      renderInput={(params) => <TextField {...params} fullWidth />}
                    />
                  </LocalizationProvider>
                </Grid>
              )}
```

- [ ] **Step 6: Render the report**

Immediately after the "Customer Whish Payments Report" block (`EnhancedReportsView.js:464-474`) and before the "Overdue Payments" block, add:

```js
        {/* Daily Cash Report */}
        {reportType === 'daily-cash' && renderDailyCashReport()}
```

- [ ] **Step 7: Build check**

Run: `cd frontend && npx react-scripts build`
Expected: build succeeds with no new errors (pre-existing warnings in unrelated files are fine — see Global Constraints).

- [ ] **Step 8: Manual verification against the local dev server**

Start both dev servers from `.claude/launch.json` (`preview_start` with `name: "backend"` for Flask on port 5000, `name: "frontend"` for CRA on port 3000). **Caution**: `.claude/launch.json`'s `backend` config has no `DATABASE_URL` override, so it points at this checkout's real dev database — do not create/mutate real customer or payment data while verifying; prefer reading existing data (an already-paid cash payment from today, if one exists) over creating new payments, or explicitly set a scratch `DATABASE_URL` for this verification session if you do need to create test data.

Log in as an admin user, open Reports, select "Daily Cash Report" from the dropdown, confirm:
- The End Date picker disappears and the Start Date picker relabels to "Date", defaulting to today.
- A day with existing paid cash payments shows a nonzero grand total and correct per-collector rows.
- Clicking a collector row's expand arrow reveals its individual payments.
- Picking a date with no cash payments shows "No cash payments collected on this day."
- Switching to a different report type and back to "Daily Cash Report" still works (state doesn't break).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/EnhancedReportsView.js
git commit -m "$(cat <<'EOF'
Add Daily Cash Report option to the Reports view

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
