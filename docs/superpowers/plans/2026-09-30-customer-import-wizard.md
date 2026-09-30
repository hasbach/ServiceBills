# Customer Import Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin bulk-import a new tenant's existing customers and their current subscription state from a generated Excel template.

**Architecture:** One new backend module `customer_import.py` (template builder, workbook reader, validator, committer, error report, route registration) wired into app.py with two lines, following the `whatsapp_inbox_routes.register_inbox_routes(app, appmod)` pattern. Frontend: one new MUI Stepper dialog `CustomerImportWizard.js`, four `apiService` methods, one button on the Subscriptions page.

**Tech Stack:** Flask + Flask-SQLAlchemy, openpyxl (new), pytest (in-memory SQLite); React 18 + MUI v5 + axios (CRA / react-scripts).

**Spec:** `docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md`

## Global Constraints

- Migration mode: import NEVER calls `send_whatsapp_message`, `_maybe_create_customer_payment_link`, or any back-billing loop.
- Billing anchor: `subscription_start_date = expiry_date - 1 billing cycle`; opening-balance Payment / ResellerPayment dated at the same anchor.
- Only `.xlsx` accepted. Max 5 MB, max 5,000 data rows.
- All endpoints: admin only (`'admin' in appmod._jwt_roles()`), tenant-scoped via `tenant_query` / `new_for_tenant`.
- Customer cap: `plans.limits(current_tenant().plan)["max_customers"]` (None = unlimited).
- Commit is one DB transaction; any exception → rollback, nothing written.
- Do not edit anything in app.py except the two registration lines in Task 1.
- Run backend tests from the repo root with `python -m pytest ...`. Python is 3.13.
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `customer_import.py` (create) | All import logic + the 4 routes |
| `app.py` (modify, 2 lines near line 9098) | Register the routes |
| `requirements.txt` (modify) | add `openpyxl` |
| `tests/test_customer_import.py` (create) | Backend tests |
| `frontend/src/context/AppContext.js` (modify) | 4 apiService methods |
| `frontend/src/components/CustomerImportWizard.js` (create) | Wizard dialog |
| `frontend/src/components/CustomerImportWizard.test.js` (create) | Jest render test |
| `frontend/src/components/SubscriptionsView.js` (modify) | Import button + dialog mount |

---

### Task 1: Dependency, module skeleton, template + reader, template route

**Files:**
- Modify: `requirements.txt`
- Create: `customer_import.py`
- Modify: `app.py` (after line 9098 `whatsapp_inbox_routes.register_inbox_routes(app, sys.modules[__name__])`)
- Create: `tests/test_customer_import.py`

**Interfaces:**
- Produces: `COLUMNS`, `HEADERS`, `REQUIRED`, `ImportFileError`, `build_template(appmod) -> bytes`, `read_workbook(file_storage) -> list[dict]` (each dict keyed by header, plus `'_row'`: 1-based sheet row), `register_customer_import_routes(app, appmod)`, route `GET /api/customers/import/template`.

- [ ] **Step 1: Install openpyxl and add it to requirements**

Append the line `openpyxl` to the end of `requirements.txt`. Then run:

```bash
python -m pip install openpyxl
```

Expected: `Successfully installed openpyxl-...` (or "already satisfied").

- [ ] **Step 2: Write the failing tests**

Create `tests/test_customer_import.py`:

```python
"""Customer import wizard -- see
docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md."""
import io
import json
from datetime import datetime, timedelta

import pytest
from openpyxl import Workbook, load_workbook

from tests.conftest import make_tenant, auth_headers
import customer_import as ci

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def _plan(client, headers, name='Basic', price=20, cycle='monthly'):
    r = client.post('/api/subscription_plans', headers=headers,
                    json={'name': name, 'price': price, 'billing_cycle': cycle})
    assert r.status_code in (200, 201), r.get_json()
    return name


def _xlsx(rows, headers=None):
    """Build an upload workbook: `rows` is a list of dicts keyed by header."""
    headers = headers or ci.HEADERS
    wb = Workbook()
    ws = wb.active
    ws.title = 'Customers'
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _future(days=20):
    return (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')


def _row(**kw):
    base = {'name': 'Ali', 'phone': '03123456', 'address': 'Beirut',
            'plan': 'Basic', 'expiry_date': _future()}
    base.update(kw)
    return base


def test_template_requires_admin(client):
    make_tenant(client, 'Biz', 'owner')
    cashier = auth_headers(client, 'cash1', role='cashier')
    r = client.get('/api/customers/import/template', headers=cashier)
    assert r.status_code == 403


def test_template_lists_tenant_plans_only(client):
    a = make_tenant(client, 'A', 'a_admin')
    _plan(client, a, 'Fiber A')
    b = make_tenant(client, 'B', 'b_admin')
    _plan(client, b, 'Fiber B')

    r = client.get('/api/customers/import/template', headers=b)
    assert r.status_code == 200
    assert r.mimetype == XLSX
    wb = load_workbook(io.BytesIO(r.data))
    assert wb.sheetnames == ['Customers', 'Instructions', 'Lists']
    assert [c.value for c in wb['Customers'][1]] == ci.HEADERS
    lists_values = {c.value for col in wb['Lists'].iter_cols() for c in col}
    assert 'Fiber B' in lists_values
    assert 'Fiber A' not in lists_values
    assert wb['Lists'].sheet_state == 'hidden'


class _Upload:
    """Minimal stand-in for werkzeug FileStorage."""
    def __init__(self, buf, filename='c.xlsx'):
        self._buf = buf
        self.filename = filename

    def read(self, n=-1):
        return self._buf.read(n)


def test_read_workbook_skips_blank_rows_and_records_row_numbers():
    buf = _xlsx([_row(name='A'), {}, _row(name='B')])
    rows = ci.read_workbook(_Upload(buf))
    assert [r['name'] for r in rows] == ['A', 'B']
    assert [r['_row'] for r in rows] == [2, 4]


def test_read_workbook_rejects_missing_required_header():
    buf = _xlsx([{'name': 'A'}], headers=['name', 'phone'])
    with pytest.raises(ci.ImportFileError, match='address'):
        ci.read_workbook(_Upload(buf))


def test_read_workbook_rejects_non_xlsx():
    with pytest.raises(ci.ImportFileError):
        ci.read_workbook(_Upload(io.BytesIO(b'name,phone\nA,1\n'), 'c.csv'))


def test_read_workbook_rejects_empty_sheet():
    with pytest.raises(ci.ImportFileError, match='no data rows'):
        ci.read_workbook(_Upload(_xlsx([])))
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_customer_import.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'customer_import'`.

- [ ] **Step 4: Create `customer_import.py`**

```python
"""Bulk customer import from an .xlsx template -- see
docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md.

Migration mode: customers arrive with their current paid-through expiry and
nothing is billed, linked to Whish, or sent on WhatsApp. Registered from
app.py via register_customer_import_routes(app, appmod) so app.py doesn't
grow further."""
import io
from functools import wraps

from flask import jsonify, request, send_file
from flask_jwt_extended import verify_jwt_in_request
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

from tenancy import tenant_query

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
SHEET_NAME = 'Customers'
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# (header, required, instruction). Order = template column order.
COLUMNS = [
    ('name', True, 'Customer full name (max 100 characters).'),
    ('phone', True, 'Phone number, kept as text so leading zeros survive (max 20).'),
    ('address', True, 'Address (max 200 characters).'),
    ('plan', True, 'Subscription plan name. Pick from the list, or type a new name -- '
                   'the wizard will ask for its price before importing.'),
    ('expiry_date', True, 'Date the customer is paid through (YYYY-MM-DD). '
                          'Billing resumes from this date.'),
    ('active', False, 'yes / no. Default yes. Inactive customers are never billed.'),
    ('sector', False, 'Area / sector. New names are created automatically.'),
    ('reseller', False, 'Reseller name, exactly as in ServiceBills. Blank for direct customers.'),
    ('discount', False, 'Discount per billing cycle (number, default 0).'),
    ('cost_override', False, 'Your cost for this customer, if different from the plan cost.'),
    ('opening_balance', False, 'Amount the customer owes you today (default 0). '
                               'Recorded as one unpaid payment.'),
    ('upstream_provider', False, 'Upstream provider name, exactly as in ServiceBills.'),
    ('upstream_username', False, 'Username on the upstream provider portal.'),
    ('network_device', False, 'Network device (MikroTik) name, exactly as in ServiceBills.'),
    ('pppoe_username', False, 'PPPoE username on that network device.'),
    ('onu_mac', False, 'ONU MAC address (aa:bb:cc:dd:ee:ff).'),
    ('cpe_mac', False, "Customer's own router MAC. One router per customer."),
    ('notes', False, 'Free text notes.'),
    ('whatsapp_enabled', False, 'yes / no. Default yes.'),
]
HEADERS = [c[0] for c in COLUMNS]
REQUIRED = [c[0] for c in COLUMNS if c[1]]
TEXT_COLUMNS = ('phone', 'upstream_username', 'pppoe_username', 'onu_mac', 'cpe_mac')
# Columns with a dropdown fed from the hidden Lists sheet. True = typing a
# value that isn't in the list is allowed (plan/sector get created on import).
LOOKUP_COLUMNS = {'plan': True, 'sector': True, 'reseller': False,
                  'upstream_provider': False, 'network_device': False}
MIGRATION_NOTES = [
    'How the import works:',
    '- Nothing is billed on import. expiry_date is the date the customer is paid through; '
    'the normal billing run charges the next cycle on that date.',
    '- A customer whose expiry_date has already passed (and is active) will be charged on the '
    'next billing run, exactly like any overdue customer.',
    '- opening_balance is what the customer owes you today; it becomes one unpaid payment.',
    '- No WhatsApp messages are sent to imported customers.',
    '- Rows that repeat a customer (same name and phone) already in ServiceBills are skipped, '
    'so you can safely upload a corrected file again.',
]


class ImportFileError(ValueError):
    """The upload as a whole is unusable (wrong type, headers, size)."""


def _lookup_names(appmod):
    """Tenant-scoped, sorted, de-duplicated names for each dropdown column."""
    models = {'plan': appmod.SubscriptionPlan, 'sector': appmod.Sector,
              'reseller': appmod.Reseller, 'upstream_provider': appmod.UpstreamProvider,
              'network_device': appmod.NetworkDevice}
    return {key: sorted({r.name for r in tenant_query(model).all() if r.name})
            for key, model in models.items()}


def build_template(appmod):
    """Return the current tenant's import template as .xlsx bytes."""
    lists = _lookup_names(appmod)
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = 'A2'
    letters = {}
    for idx, header in enumerate(HEADERS, start=1):
        letter = ws.cell(row=1, column=idx).column_letter
        letters[header] = letter
        ws.column_dimensions[letter].width = max(14, len(header) + 4)
        if header in TEXT_COLUMNS:
            ws.column_dimensions[letter].number_format = '@'
    last_row = MAX_ROWS + 1

    ins = wb.create_sheet('Instructions')
    ins.append(['Column', 'Required', 'What to enter'])
    for cell in ins[1]:
        cell.font = Font(bold=True)
    for header, required, help_text in COLUMNS:
        ins.append([header, 'yes' if required else '', help_text])
    ins.append([])
    for line in MIGRATION_NOTES:
        ins.append([line])
    ins.column_dimensions['A'].width = 20
    ins.column_dimensions['C'].width = 90

    lists_ws = wb.create_sheet('Lists')
    for col, (key, allow_new) in enumerate(LOOKUP_COLUMNS.items(), start=1):
        values = lists[key]
        lists_ws.cell(row=1, column=col, value=key)
        for i, value in enumerate(values, start=2):
            lists_ws.cell(row=i, column=col, value=value)
        if not values:
            continue
        src = lists_ws.cell(row=1, column=col).column_letter
        dv = DataValidation(type='list',
                            formula1=f'=Lists!${src}$2:${src}${len(values) + 1}',
                            allow_blank=True, showErrorMessage=not allow_new)
        ws.add_data_validation(dv)
        dv.add(f'{letters[key]}2:{letters[key]}{last_row}')
    for key in ('active', 'whatsapp_enabled'):
        dv = DataValidation(type='list', formula1='"yes,no"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f'{letters[key]}2:{letters[key]}{last_row}')
    lists_ws.sheet_state = 'hidden'

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def read_workbook(file_storage):
    """Parse an uploaded template into a list of {header: value} dicts, each
    with '_row' = its 1-based sheet row. Fully blank rows are skipped.
    Raises ImportFileError when the file as a whole is unusable."""
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        raise ImportFileError('No file uploaded.')
    data = file_storage.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError('File is larger than 5 MB.')
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportFileError('Could not read the file. Upload the .xlsx template (Excel format).')
    try:
        if SHEET_NAME not in wb.sheetnames:
            raise ImportFileError(f"The workbook has no '{SHEET_NAME}' sheet. "
                                  f"Start from the downloaded template.")
        rows = wb[SHEET_NAME].iter_rows(values_only=True)
        header_row = next(rows, None) or ()
        headers = [str(h).strip().lower() if h is not None else '' for h in header_row]
        missing = [h for h in REQUIRED if h not in headers]
        if missing:
            raise ImportFileError('Missing required column(s): ' + ', '.join(missing))
        index = {h: i for i, h in enumerate(headers) if h in HEADERS}
        out = []
        for row_number, values in enumerate(rows, start=2):
            if not values or all(_is_blank(v) for v in values):
                continue
            if len(out) >= MAX_ROWS:
                raise ImportFileError(f'Too many rows: the limit is {MAX_ROWS} customers per import.')
            record = {h: (values[i] if i < len(values) else None) for h, i in index.items()}
            record['_row'] = row_number
            out.append(record)
    finally:
        wb.close()
    if not out:
        raise ImportFileError('The Customers sheet has no data rows.')
    return out


def register_customer_import_routes(app, appmod):
    def import_admin(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            if 'admin' not in appmod._jwt_roles():
                return jsonify(msg="Admins only!"), 403
            return fn(*args, **kwargs)
        return wrapper

    @app.route('/api/customers/import/template', methods=['GET'])
    @import_admin
    def customer_import_template():
        return send_file(io.BytesIO(build_template(appmod)), mimetype=XLSX_MIME,
                         as_attachment=True, download_name='customer-import-template.xlsx')
```

Note: `register_customer_import_routes` keeps growing in Tasks 2 and 3 (they add routes inside it, reusing `import_admin`).

- [ ] **Step 5: Register the routes in app.py**

Directly after line 9098 (`whatsapp_inbox_routes.register_inbox_routes(app, sys.modules[__name__])`) add:

```python
import customer_import
customer_import.register_customer_import_routes(app, sys.modules[__name__])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_customer_import.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt customer_import.py app.py tests/test_customer_import.py
git commit -m "feat(import): customer import template download

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Row validation + validate endpoint

**Files:**
- Modify: `customer_import.py`
- Modify: `tests/test_customer_import.py` (append)

**Interfaces:**
- Consumes: `read_workbook`, `ImportFileError`, `HEADERS`, `import_admin` (inside `register_customer_import_routes`).
- Uses from app.py via `appmod`: `Customer`, `SubscriptionPlan`, `Sector`, `Reseller`, `UpstreamProvider`, `NetworkDevice`, `Currency`, `_validate_mac_address(raw, allow_empty=True) -> (mac|None, err|None)`, `_check_cpe_mac_available(mac) -> err|None`, `_check_network_link_conflict(None, device_id, pppoe, provider_id, upstream_user) -> err|None`, `_clean_customer_notes(raw) -> str|None`.
- Produces:
  - `normalize_new_plans(appmod, raw) -> (dict[str_lower_name, dict], list[str] errors)`; `raw` is a JSON string, list, or None. Each value: `{'name', 'price', 'cost', 'billing_cycle', 'currency'}`.
  - `validate_rows(appmod, raw_rows, plan_defs=None) -> {'rows': [...], 'summary': {...}}`. `plan_defs=None` means "validate stage" (unknown plans are warnings); a dict (possibly empty) means "commit stage" (unknown plan without a definition is an error).
  - Each row: `{'row': int, 'status': 'ok'|'warning'|'error', 'import': bool, 'messages': [str], 'data': {...JSON-safe...}, 'resolved': {...python objects, commit only...}}`.
  - `summary`: `{'total', 'ok', 'warning', 'error', 'importable', 'unknown_plans': [names], 'new_sectors': [names], 'customer_limit': int|None, 'existing_customers': int}`.
  - `public_rows(result) -> list` = rows without the `'resolved'` key.
  - `cycle_delta(billing_cycle) -> relativedelta`.
  - Route `POST /api/customers/import/validate` (multipart `file`) → 200 `{'rows': public_rows, 'summary'}`, or 400 `{'error': str}` for ImportFileError.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_customer_import.py`:

```python
def _validate(client, headers, rows):
    return client.post('/api/customers/import/validate', headers=headers,
                       data={'file': (_xlsx(rows), 'c.xlsx')},
                       content_type='multipart/form-data')


def test_validate_ok_row(client):
    h = make_tenant(client, 'Biz', 'v_ok')
    _plan(client, h)
    r = _validate(client, h, [_row()])
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['summary']['ok'] == 1 and body['summary']['importable'] == 1
    row = body['rows'][0]
    assert row['status'] == 'ok' and row['import'] is True and row['row'] == 2
    assert row['data']['plan'] == 'Basic'
    assert 'resolved' not in row


def test_validate_plan_match_is_case_insensitive(client):
    h = make_tenant(client, 'Biz', 'v_case')
    _plan(client, h, 'Fiber 50M')
    body = _validate(client, h, [_row(plan='  fiber 50m ')]).get_json()
    assert body['rows'][0]['status'] == 'ok'
    assert body['summary']['unknown_plans'] == []


def test_validate_missing_required_and_bad_values(client):
    h = make_tenant(client, 'Biz', 'v_bad')
    _plan(client, h)
    body = _validate(client, h, [
        _row(name=''),
        _row(expiry_date='not a date'),
        _row(discount=-5),
        _row(active='maybe'),
        _row(onu_mac='zz'),
        _row(pppoe_username='u1'),
    ]).get_json()
    statuses = [r['status'] for r in body['rows']]
    assert statuses == ['error'] * 6
    assert all(r['import'] is False for r in body['rows'])
    assert 'name is required' in ' '.join(body['rows'][0]['messages'])
    assert 'network_device' in ' '.join(body['rows'][5]['messages'])


def test_validate_unknown_plan_is_warning_and_listed(client):
    h = make_tenant(client, 'Biz', 'v_newplan')
    body = _validate(client, h, [_row(plan='Fiber 50M'), _row(name='B', plan='fiber 50m')]).get_json()
    assert body['summary']['unknown_plans'] == ['Fiber 50M']
    assert [r['status'] for r in body['rows']] == ['warning', 'warning']
    assert all(r['import'] for r in body['rows'])


def test_validate_unknown_reseller_is_error(client):
    h = make_tenant(client, 'Biz', 'v_res')
    _plan(client, h)
    body = _validate(client, h, [_row(reseller='Nobody')]).get_json()
    assert body['rows'][0]['status'] == 'error'


def test_validate_new_sector_listed(client):
    h = make_tenant(client, 'Biz', 'v_sector')
    _plan(client, h)
    body = _validate(client, h, [_row(sector='Hamra'), _row(name='B', sector='hamra')]).get_json()
    assert body['summary']['new_sectors'] == ['Hamra']
    assert body['rows'][0]['status'] == 'ok'


def test_validate_duplicates_in_sheet_and_db(client):
    h = make_tenant(client, 'Biz', 'v_dup')
    _plan(client, h)
    plan_id = client.get('/api/subscription_plans', headers=h).get_json()[0]['id']
    client.post('/api/customers', headers=h, json={
        'name': 'Existing', 'phone': '111', 'address': 'x', 'subscription_plan_id': plan_id})
    body = _validate(client, h, [
        _row(name='Existing', phone='111'),
        _row(name='Ali', phone='222'),
        _row(name='ali', phone='222'),
    ]).get_json()
    rows = body['rows']
    assert rows[0]['status'] == 'warning' and rows[0]['import'] is False
    assert rows[1]['status'] == 'ok' and rows[1]['import'] is True
    assert rows[2]['status'] == 'warning' and rows[2]['import'] is False
    assert 'row 3' in ' '.join(rows[2]['messages'])


def test_validate_duplicate_cpe_mac_in_sheet(client):
    h = make_tenant(client, 'Biz', 'v_mac')
    _plan(client, h)
    body = _validate(client, h, [
        _row(name='A', phone='1', cpe_mac='AA:BB:CC:DD:EE:01'),
        _row(name='B', phone='2', cpe_mac='aa-bb-cc-dd-ee-01'),
    ]).get_json()
    assert [r['status'] for r in body['rows']] == ['ok', 'error']


def test_validate_expired_active_is_warning_but_importable(client):
    h = make_tenant(client, 'Biz', 'v_exp')
    _plan(client, h)
    past = (datetime.utcnow() - timedelta(days=10)).strftime('%Y-%m-%d')
    body = _validate(client, h, [_row(expiry_date=past)]).get_json()
    row = body['rows'][0]
    assert row['status'] == 'warning' and row['import'] is True


def test_validate_accepts_excel_date_cells_and_dmy_text(client):
    h = make_tenant(client, 'Biz', 'v_dates')
    _plan(client, h)
    body = _validate(client, h, [
        _row(name='A', phone='1', expiry_date=datetime(2030, 1, 15)),
        _row(name='B', phone='2', expiry_date='15/01/2030'),
    ]).get_json()
    assert [r['data']['expiry_date'] for r in body['rows']] == ['2030-01-15', '2030-01-15']


def test_validate_customer_cap(client, monkeypatch):
    import plans
    monkeypatch.setitem(plans.PLANS['free'], 'max_customers', 2)
    h = make_tenant(client, 'Biz', 'v_cap')
    _plan(client, h)
    body = _validate(client, h, [_row(name=f'C{i}', phone=str(i)) for i in range(3)]).get_json()
    assert [r['status'] for r in body['rows']] == ['ok', 'ok', 'error']
    assert body['summary']['customer_limit'] == 2


def test_validate_bad_file_is_400(client):
    h = make_tenant(client, 'Biz', 'v_file')
    r = client.post('/api/customers/import/validate', headers=h,
                    data={'file': (io.BytesIO(b'hello'), 'c.xlsx')},
                    content_type='multipart/form-data')
    assert r.status_code == 400 and 'error' in r.get_json()


def test_validate_requires_admin(client):
    make_tenant(client, 'Biz', 'v_admin')
    cashier = auth_headers(client, 'v_cash', role='cashier')
    assert _validate(client, cashier, [_row()]).status_code == 403


def test_validate_does_not_see_other_tenant_resellers(client):
    a = make_tenant(client, 'A', 'iso_a')
    r = client.post('/api/resellers', headers=a, json={'name': 'Shared Name', 'phone': '1', 'type': 'type1'})
    assert r.status_code in (200, 201), r.get_json()
    b = make_tenant(client, 'B', 'iso_b')
    _plan(client, b)
    body = _validate(client, b, [_row(reseller='Shared Name')]).get_json()
    assert body['rows'][0]['status'] == 'error'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_customer_import.py -v`
Expected: the new `test_validate_*` tests FAIL (404 on the route). Task 1 tests still pass. If a test fails at an `/api/resellers` POST assertion, open `app.py`, search for `route('/api/resellers', methods=['POST']` and adjust the JSON payload to that endpoint's required fields — do not change the endpoint.

- [ ] **Step 3: Add the validation code to `customer_import.py`**

Add these imports at the top (merge with the existing import block):

```python
import json
import math
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

import plans
from tenancy import current_tenant, tenant_query
```

Add below `read_workbook` (above `register_customer_import_routes`):

```python
_TRUE = {'yes', 'y', 'true', '1'}
_FALSE = {'no', 'n', 'false', '0'}
_DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d')
_MAX_LEN = {'name': 100, 'phone': 20, 'address': 200, 'sector': 100}


def cycle_delta(billing_cycle):
    """One billing cycle, matching _renew_subscription_core / the scheduler."""
    return relativedelta(years=1) if billing_cycle == 'yearly' else relativedelta(months=1)


def _text(value):
    """Cell -> trimmed string. Whole floats lose the '.0' Excel gives numbers."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _key(value):
    return _text(value).lower()


def _bool(value, default, field, errors):
    if isinstance(value, bool):
        return value
    s = _key(value)
    if not s:
        return default
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    errors.append(f"{field} must be yes or no (got '{_text(value)}').")
    return default


def _number(value, field, errors):
    s = _text(value).replace(',', '')
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        errors.append(f'{field} must be a number.')
        return None
    if not math.isfinite(n) or n < 0:
        errors.append(f'{field} cannot be negative.')
        return None
    return n


def _date(value):
    if isinstance(value, datetime):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = _text(value)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _by_name(rows):
    index = {}
    for r in rows:
        index.setdefault(_key(r.name), []).append(r)
    return index


def _resolve(index, value, label, errors):
    """Find exactly one tenant row by case-insensitive name, else record an error."""
    if not _text(value):
        return None
    matches = index.get(_key(value), [])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        errors.append(f"{label} '{_text(value)}' not found. Create it in ServiceBills first "
                      f"or fix the spelling.")
    else:
        errors.append(f"{label} name '{_text(value)}' matches {len(matches)} records; "
                      f"rename them so it is unique.")
    return None


def normalize_new_plans(appmod, raw):
    """Parse the wizard's new-plan definitions. Returns ({lower_name: def}, errors)."""
    if raw in (None, ''):
        return {}, []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}, ['new_plans is not valid JSON.']
    if not isinstance(items, list):
        return {}, ['new_plans must be a list.']
    out, errors = {}, []
    for item in items:
        if not isinstance(item, dict):
            errors.append('Each new plan must be an object.')
            continue
        name = _text(item.get('name'))
        if not name:
            errors.append('A new plan is missing its name.')
            continue
        price = _number(item.get('price'), f"Price of plan '{name}'", errors)
        cost = _number(item.get('cost'), f"Cost of plan '{name}'", errors) or 0.0
        cycle = _text(item.get('billing_cycle')) or 'monthly'
        currency = (_text(item.get('currency')) or 'USD').upper()
        if price is None:
            errors.append(f"Plan '{name}' needs a price.")
            continue
        if cycle not in ('monthly', 'yearly'):
            errors.append(f"Plan '{name}': billing cycle must be monthly or yearly.")
            continue
        if not appmod.Currency.query.filter_by(code=currency, active=True).first():
            errors.append(f"Plan '{name}': unknown currency '{currency}'.")
            continue
        out[name.lower()] = {'name': name, 'price': price, 'cost': cost,
                             'billing_cycle': cycle, 'currency': currency}
    return out, errors


def validate_rows(appmod, raw_rows, plan_defs=None):
    """Validate parsed rows against the current tenant. plan_defs=None is the
    preview stage (unknown plans are warnings); a dict is the commit stage
    (unknown plans without a definition are errors). Never writes."""
    Customer = appmod.Customer
    plan_index = _by_name(tenant_query(appmod.SubscriptionPlan).all())
    sector_index = {_key(s.name): s.name for s in tenant_query(appmod.Sector).all()}
    reseller_index = _by_name(tenant_query(appmod.Reseller).all())
    upstream_index = _by_name(tenant_query(appmod.UpstreamProvider).all())
    device_index = _by_name(tenant_query(appmod.NetworkDevice).all())
    existing_people = {(_key(n), _text(p)) for n, p in
                       tenant_query(Customer).with_entities(Customer.name, Customer.phone).all()}
    existing_count = tenant_query(Customer).count()
    limit = plans.limits(current_tenant().plan)['max_customers']
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    seen_people, seen_cpe, seen_pppoe, seen_upstream = {}, {}, {}, {}
    unknown_plans, new_sectors = {}, {}
    rows = []

    for raw in raw_rows:
        errors, warnings = [], []
        row_no = raw['_row']
        skip_duplicate = False

        values = {}
        for field in ('name', 'phone', 'address'):
            values[field] = _text(raw.get(field))
            if not values[field]:
                errors.append(f'{field} is required.')
        for field, limit_len in _MAX_LEN.items():
            if len(_text(raw.get(field))) > limit_len:
                errors.append(f'{field} is longer than {limit_len} characters.')
        if isinstance(raw.get('phone'), (int, float)) and not isinstance(raw.get('phone'), bool):
            warnings.append('phone was stored as a number in Excel; check no leading zero was lost.')

        plan_name = _text(raw.get('plan'))
        plan = None
        plan_key = plan_name.lower()
        billing_cycle = None
        if not plan_name:
            errors.append('plan is required.')
        else:
            matches = plan_index.get(plan_key, [])
            if len(matches) == 1:
                plan = matches[0]
                billing_cycle = plan.billing_cycle
            elif len(matches) > 1:
                errors.append(f"Plan name '{plan_name}' matches {len(matches)} plans; "
                              f"rename them so it is unique.")
            else:
                unknown_plans.setdefault(plan_key, plan_name)
                if plan_defs is None:
                    warnings.append(f"New plan '{plan_name}' - you will set its price before importing.")
                elif plan_key in plan_defs:
                    billing_cycle = plan_defs[plan_key]['billing_cycle']
                else:
                    errors.append(f"Plan '{plan_name}' does not exist and no price was given for it.")

        expiry = None
        if _is_blank(raw.get('expiry_date')):
            errors.append('expiry_date is required.')
        else:
            expiry = _date(raw.get('expiry_date'))
            if expiry is None:
                errors.append(f"expiry_date '{_text(raw.get('expiry_date'))}' is not a date "
                              f"(use YYYY-MM-DD).")

        active = _bool(raw.get('active'), True, 'active', errors)
        whatsapp_enabled = _bool(raw.get('whatsapp_enabled'), True, 'whatsapp_enabled', errors)
        discount = _number(raw.get('discount'), 'discount', errors) or 0.0
        cost_override = _number(raw.get('cost_override'), 'cost_override', errors)
        opening_balance = _number(raw.get('opening_balance'), 'opening_balance', errors) or 0.0

        sector_name = _text(raw.get('sector')) or None
        if sector_name:
            if _key(sector_name) in sector_index:
                sector_name = sector_index[_key(sector_name)]
            else:
                sector_name = new_sectors.setdefault(_key(sector_name), sector_name)

        reseller = _resolve(reseller_index, raw.get('reseller'), 'Reseller', errors)
        provider = _resolve(upstream_index, raw.get('upstream_provider'), 'Upstream provider', errors)
        device = _resolve(device_index, raw.get('network_device'), 'Network device', errors)
        upstream_username = _text(raw.get('upstream_username')) or None
        pppoe_username = _text(raw.get('pppoe_username')) or None
        if upstream_username and not _text(raw.get('upstream_provider')):
            errors.append('upstream_username needs an upstream_provider.')
        if pppoe_username and not _text(raw.get('network_device')):
            errors.append('pppoe_username needs a network_device.')

        onu_mac, err = appmod._validate_mac_address(_text(raw.get('onu_mac')), allow_empty=True)
        if err:
            errors.append(f'onu_mac: {err}')
        cpe_mac, err = appmod._validate_mac_address(_text(raw.get('cpe_mac')), allow_empty=True)
        if err:
            errors.append(f'cpe_mac: {err}')
        if cpe_mac:
            err = appmod._check_cpe_mac_available(cpe_mac)
            if err:
                errors.append(err)
            elif cpe_mac in seen_cpe:
                errors.append(f'cpe_mac {cpe_mac} is also on row {seen_cpe[cpe_mac]}.')
            seen_cpe.setdefault(cpe_mac, row_no)

        err = appmod._check_network_link_conflict(
            None, device.id if device else None, pppoe_username,
            provider.id if provider else None, upstream_username)
        if err:
            errors.append(err)
        if device and pppoe_username:
            k = (device.id, pppoe_username)
            if k in seen_pppoe:
                errors.append(f"pppoe_username '{pppoe_username}' is also on row {seen_pppoe[k]}.")
            seen_pppoe.setdefault(k, row_no)
        if provider and upstream_username:
            k = (provider.id, upstream_username)
            if k in seen_upstream:
                errors.append(f"upstream_username '{upstream_username}' is also on row {seen_upstream[k]}.")
            seen_upstream.setdefault(k, row_no)

        person = (_key(values['name']), values['phone'])
        if values['name'] and values['phone']:
            if person in existing_people:
                warnings.append('Duplicate: a customer with this name and phone already exists - will be skipped.')
                skip_duplicate = True
            elif person in seen_people:
                warnings.append(f'Duplicate of row {seen_people[person]} - will be skipped.')
                skip_duplicate = True
            else:
                seen_people[person] = row_no

        if expiry and active and expiry < today:
            warnings.append('Expiry date has passed - the customer will be charged on the next billing run.')

        rows.append({
            'row': row_no,
            'errors': errors,
            'warnings': warnings,
            'import': not errors and not skip_duplicate,
            'data': {
                'name': values['name'], 'phone': values['phone'], 'address': values['address'],
                'plan': plan.name if plan else plan_name,
                'expiry_date': expiry.strftime('%Y-%m-%d') if expiry else _text(raw.get('expiry_date')),
                'active': active, 'sector': sector_name,
                'reseller': reseller.name if reseller else _text(raw.get('reseller')) or None,
                'opening_balance': opening_balance,
            },
            'resolved': {
                'name': values['name'], 'phone': values['phone'], 'address': values['address'],
                'plan': plan, 'plan_key': plan_key, 'billing_cycle': billing_cycle,
                'expiry': expiry, 'active': active, 'sector': sector_name,
                'reseller': reseller, 'discount': discount, 'cost_override': cost_override,
                'opening_balance': opening_balance,
                'upstream_provider_id': provider.id if provider else None,
                'upstream_username': upstream_username,
                'network_device_id': device.id if device else None,
                'pppoe_username': pppoe_username,
                'onu_mac': onu_mac, 'cpe_mac': cpe_mac,
                'notes': appmod._clean_customer_notes(raw.get('notes')),
                'whatsapp_enabled': whatsapp_enabled,
            },
        })

    # Customer cap: count only rows that would actually be imported, in sheet order.
    if limit is not None:
        room = max(0, limit - existing_count)
        for r in rows:
            if not r['import']:
                continue
            if room > 0:
                room -= 1
            else:
                r['errors'].append(f'Customer limit ({limit}) for your plan reached - upgrade to import more.')
                r['import'] = False

    for r in rows:
        r['status'] = 'error' if r['errors'] else ('warning' if r['warnings'] else 'ok')
        r['messages'] = r.pop('errors') + r.pop('warnings')

    summary = {
        'total': len(rows),
        'ok': sum(r['status'] == 'ok' for r in rows),
        'warning': sum(r['status'] == 'warning' for r in rows),
        'error': sum(r['status'] == 'error' for r in rows),
        'importable': sum(r['import'] for r in rows),
        'unknown_plans': sorted(unknown_plans.values(), key=str.lower),
        'new_sectors': sorted(new_sectors.values(), key=str.lower),
        'customer_limit': limit,
        'existing_customers': existing_count,
    }
    return {'rows': rows, 'summary': summary}


def public_rows(result):
    return [{k: v for k, v in r.items() if k != 'resolved'} for r in result['rows']]
```

Inside `register_customer_import_routes`, after `customer_import_template`, add:

```python
    @app.route('/api/customers/import/validate', methods=['POST'])
    @import_admin
    def customer_import_validate():
        try:
            result = validate_rows(appmod, read_workbook(request.files.get('file')))
        except ImportFileError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify({'rows': public_rows(result), 'summary': result['summary']})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_customer_import.py -v`
Expected: all pass (20 tests).

- [ ] **Step 5: Commit**

```bash
git add customer_import.py tests/test_customer_import.py
git commit -m "feat(import): validate customer import rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Commit import + error report endpoints

**Files:**
- Modify: `customer_import.py`
- Modify: `tests/test_customer_import.py` (append)

**Interfaces:**
- Consumes: `read_workbook`, `validate_rows`, `normalize_new_plans`, `cycle_delta`, `HEADERS`, `import_admin`.
- Uses from app.py via `appmod`: `db`, `Customer`, `SubscriptionPlan`, `Sector`, `Payment`, `ResellerPayment`, `recalculate_estimated_profit(tenant_id)`.
- Produces:
  - `commit_import(appmod, result, plan_defs) -> dict` `{'imported', 'skipped', 'plans_created', 'sectors_created', 'skipped_rows': [{'row', 'messages'}]}`. Caller handles rollback.
  - `build_error_report(raw_rows, result) -> bytes` (.xlsx of non-imported rows + `errors` column).
  - `POST /api/customers/import/commit` (multipart `file`, form field `new_plans` JSON string, default `[]`).
  - `POST /api/customers/import/error_report` (same inputs) → .xlsx download.

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_customer_import.py`:

```python
import app as appmod


def _commit(client, headers, rows, new_plans=None):
    return client.post('/api/customers/import/commit', headers=headers,
                       data={'file': (_xlsx(rows), 'c.xlsx'),
                             'new_plans': json.dumps(new_plans or [])},
                       content_type='multipart/form-data')


def _tenant_id(client, headers):
    with client.application.app_context():
        from flask_jwt_extended import decode_token
        return decode_token(headers['Authorization'].split()[1])['tenant_id']


@pytest.fixture
def no_whatsapp(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('import must not send WhatsApp')
    monkeypatch.setattr(appmod, 'send_whatsapp_message', boom)
    monkeypatch.setattr(appmod, '_maybe_create_customer_payment_link', boom)


def test_commit_creates_customers_without_billing(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_basic')
    _plan(client, h, 'Basic', price=20)
    expiry = datetime.utcnow() + timedelta(days=20)
    r = _commit(client, h, [_row(expiry_date=expiry.strftime('%Y-%m-%d'))])
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['imported'] == 1
    tid = _tenant_id(client, h)
    with client.application.app_context():
        c = appmod.Customer.query.filter_by(tenant_id=tid).one()
        assert c.subscription_expiry_date.date() == expiry.date()
        assert c.balance == 0
        assert appmod.Payment.query.filter_by(customer_id=c.id).count() == 0


def test_scheduler_does_not_backbill_imported_customer(client, no_whatsapp):
    """The billing-anchor rule: next charge lands on expiry, not before."""
    h = make_tenant(client, 'Biz', 'c_anchor')
    _plan(client, h, 'Basic', price=20)
    _plan(client, h, 'Yearly', price=200, cycle='yearly')
    expiry = datetime.utcnow() + timedelta(days=20)
    rows = [_row(name='M', phone='1', expiry_date=expiry.strftime('%Y-%m-%d')),
            _row(name='Y', phone='2', plan='Yearly', expiry_date=expiry.strftime('%Y-%m-%d')),
            _row(name='O', phone='3', opening_balance=35, expiry_date=expiry.strftime('%Y-%m-%d'))]
    assert _commit(client, h, rows).status_code == 200
    tid = _tenant_id(client, h)
    with client.application.app_context():
        before = appmod.Payment.query.filter_by(tenant_id=tid).count()
        appmod.generate_missing_payments(tid)
        assert appmod.Payment.query.filter_by(tenant_id=tid).count() == before
        for c in appmod.Customer.query.filter_by(tenant_id=tid).all():
            assert c.subscription_expiry_date.date() == expiry.date()


def test_commit_opening_balance_direct_customer(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_open')
    _plan(client, h)
    assert _commit(client, h, [_row(opening_balance=35)]).status_code == 200
    tid = _tenant_id(client, h)
    with client.application.app_context():
        c = appmod.Customer.query.filter_by(tenant_id=tid).one()
        p = appmod.Payment.query.filter_by(customer_id=c.id).one()
        assert p.amount == 35 and p.paid is False
        assert c.balance == -35
        assert p.date == c.subscription_start_date


def test_commit_opening_balance_reseller_customer(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_resel')
    _plan(client, h)
    r = client.post('/api/resellers', headers=h, json={'name': 'Rami', 'phone': '1', 'type': 'type1'})
    assert r.status_code in (200, 201), r.get_json()
    assert _commit(client, h, [_row(reseller='rami', opening_balance=40)]).status_code == 200
    tid = _tenant_id(client, h)
    with client.application.app_context():
        reseller = appmod.Reseller.query.filter_by(tenant_id=tid).one()
        assert reseller.balance == 40
        rp = appmod.ResellerPayment.query.filter_by(reseller_id=reseller.id).one()
        assert rp.type == 'credit_added' and rp.amount == 40
        assert appmod.Payment.query.filter_by(tenant_id=tid).count() == 0


def test_commit_creates_new_plan_and_sector(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_newplan')
    r = _commit(client, h, [_row(plan='Fiber 50M', sector='Hamra')],
                new_plans=[{'name': 'Fiber 50M', 'price': 25, 'billing_cycle': 'yearly'}])
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['plans_created'] == 1 and body['sectors_created'] == 1
    tid = _tenant_id(client, h)
    with client.application.app_context():
        plan = appmod.SubscriptionPlan.query.filter_by(tenant_id=tid, name='Fiber 50M').one()
        assert plan.billing_cycle == 'yearly' and plan.price == 25
        assert appmod.Sector.query.filter_by(tenant_id=tid, name='Hamra').count() == 1
        c = appmod.Customer.query.filter_by(tenant_id=tid).one()
        assert c.sector == 'Hamra'
        assert c.subscription_expiry_date.year - c.subscription_start_date.year == 1


def test_commit_unknown_plan_without_definition_is_skipped(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_noplan')
    _plan(client, h)
    body = _commit(client, h, [_row(name='A', phone='1'), _row(name='B', phone='2', plan='Mystery')]).get_json()
    assert body['imported'] == 1 and body['skipped'] == 1
    assert body['skipped_rows'][0]['row'] == 3


def test_commit_invalid_plan_definition_is_400(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_badplan')
    r = _commit(client, h, [_row(plan='X')], new_plans=[{'name': 'X', 'price': 5, 'billing_cycle': 'weekly'}])
    assert r.status_code == 400


def test_reupload_skips_already_imported(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_reup')
    _plan(client, h)
    rows = [_row(name='A', phone='1'), _row(name='B', phone='2')]
    assert _commit(client, h, rows).get_json()['imported'] == 2
    body = _commit(client, h, rows + [_row(name='C', phone='3')]).get_json()
    assert body['imported'] == 1 and body['skipped'] == 2


def test_commit_rolls_back_on_failure(client, no_whatsapp, monkeypatch):
    """A failure after the first customer was flushed must leave nothing behind."""
    h = make_tenant(client, 'Biz', 'c_rollback')
    _plan(client, h)
    real = ci.cycle_delta
    calls = {'n': 0}
    def fail_on_second(cycle):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('boom')
        return real(cycle)
    monkeypatch.setattr(ci, 'cycle_delta', fail_on_second)
    r = _commit(client, h, [_row(name='A', phone='1'), _row(name='B', phone='2')])
    assert r.status_code == 400
    assert 'nothing was saved' in r.get_json()['error']
    tid = _tenant_id(client, h)
    with client.application.app_context():
        assert appmod.Customer.query.filter_by(tenant_id=tid).count() == 0


def test_commit_requires_admin(client):
    make_tenant(client, 'Biz', 'c_admin')
    cashier = auth_headers(client, 'c_cash', role='cashier')
    assert _commit(client, cashier, [_row()]).status_code == 403


def test_error_report_contains_only_skipped_rows(client):
    h = make_tenant(client, 'Biz', 'c_report')
    _plan(client, h)
    r = client.post('/api/customers/import/error_report', headers=h,
                    data={'file': (_xlsx([_row(name='Good', phone='1'), _row(name='', phone='2')]), 'c.xlsx'),
                          'new_plans': '[]'},
                    content_type='multipart/form-data')
    assert r.status_code == 200
    assert r.mimetype == XLSX
    ws = load_workbook(io.BytesIO(r.data))['Customers']
    header = [c.value for c in ws[1]]
    assert header == ci.HEADERS + ['errors']
    data_rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert len(data_rows) == 1
    assert data_rows[0][header.index('phone')] == '2'
    assert 'name is required' in data_rows[0][-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_customer_import.py -v`
Expected: new commit / error_report tests FAIL (404). Earlier tests pass.

- [ ] **Step 3: Add commit + error report to `customer_import.py`**

Add `from tenancy import new_for_tenant, current_tenant_id` to the tenancy import line (so it reads `from tenancy import current_tenant, current_tenant_id, new_for_tenant, tenant_query`).

Add below `public_rows`:

```python
def commit_import(appmod, result, plan_defs):
    """Write every importable row in the caller's transaction and commit once.
    Migration mode: no back-billing, no Whish links, no WhatsApp. The caller
    rolls back on any exception."""
    db = appmod.db
    importable = [r for r in result['rows'] if r['import']]

    created_plans = {}
    used_plan_keys = {r['resolved']['plan_key'] for r in importable if r['resolved']['plan'] is None}
    for key in sorted(used_plan_keys):
        d = plan_defs[key]
        plan = new_for_tenant(appmod.SubscriptionPlan, name=d['name'], price=d['price'],
                              cost=d['cost'], billing_cycle=d['billing_cycle'],
                              currency=d['currency'], status='active')
        db.session.add(plan)
        created_plans[key] = plan
    db.session.flush()

    existing_sectors = {_key(s.name) for s in tenant_query(appmod.Sector).all()}
    sectors_created = 0
    for r in importable:
        name = r['resolved']['sector']
        if name and _key(name) not in existing_sectors:
            db.session.add(new_for_tenant(appmod.Sector, name=name))
            existing_sectors.add(_key(name))
            sectors_created += 1

    for r in importable:
        v = r['resolved']
        plan = v['plan'] or created_plans[v['plan_key']]
        # Billing anchor: the scheduler bills from the last payment date, or
        # from subscription_start_date when there is none, one cycle at a time.
        # Anchoring one cycle before expiry makes the next charge land on expiry.
        anchor = v['expiry'] - cycle_delta(plan.billing_cycle)
        reseller = v['reseller']
        customer = new_for_tenant(
            appmod.Customer,
            name=v['name'], phone=v['phone'], address=v['address'], sector=v['sector'],
            subscription_plan_id=plan.id,
            subscription_start_date=anchor, subscription_expiry_date=v['expiry'],
            is_subscription_active=v['active'], balance=0.0,
            discount=v['discount'], cost_override=v['cost_override'],
            reseller_id=reseller.id if reseller else None,
            upstream_provider_id=v['upstream_provider_id'], upstream_username=v['upstream_username'],
            network_device_id=v['network_device_id'], pppoe_username=v['pppoe_username'],
            onu_mac_address=v['onu_mac'], cpe_mac_address=v['cpe_mac'],
            notes=v['notes'], whatsapp_notifications_enabled=v['whatsapp_enabled'],
        )
        db.session.add(customer)
        db.session.flush()

        amount = v['opening_balance']
        if amount > 0:
            if reseller:
                reseller.balance = (reseller.balance or 0.0) + amount
                db.session.add(new_for_tenant(
                    appmod.ResellerPayment, reseller_id=reseller.id, customer_id=customer.id,
                    amount=amount, type='credit_added', date=anchor,
                    description=f'Opening balance (import) for customer {customer.name}'))
            else:
                db.session.add(new_for_tenant(
                    appmod.Payment, customer_id=customer.id, amount=amount,
                    paid=False, date=anchor, pre_payment=False))
                customer.balance -= amount

    db.session.commit()
    # Saved already: a failure here must not make the route report "nothing was saved".
    try:
        appmod.recalculate_estimated_profit(current_tenant_id())
    except Exception:
        appmod.traceback.print_exc()

    skipped = [r for r in result['rows'] if not r['import']]
    return {
        'imported': len(importable),
        'skipped': len(skipped),
        'plans_created': len(created_plans),
        'sectors_created': sectors_created,
        'skipped_rows': [{'row': r['row'], 'messages': r['messages']} for r in skipped],
    }


def build_error_report(raw_rows, result):
    """An .xlsx of every row that was not imported, in template column order,
    plus an 'errors' column, ready to fix and re-upload."""
    by_row = {raw['_row']: raw for raw in raw_rows}
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    ws.append(HEADERS + ['errors'])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in result['rows']:
        if r['import']:
            continue
        raw = by_row[r['row']]
        values = []
        for h in HEADERS:
            v = raw.get(h)
            values.append(_text(v) if h in TEXT_COLUMNS and v is not None else v)
        ws.append(values + [' | '.join(r['messages'])])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

Inside `register_customer_import_routes`, after `customer_import_validate`, add:

```python
    def _parse_for_commit():
        raw_rows = read_workbook(request.files.get('file'))
        plan_defs, plan_errors = normalize_new_plans(appmod, request.form.get('new_plans', '[]'))
        if plan_errors:
            raise ImportFileError(' '.join(plan_errors))
        return raw_rows, plan_defs, validate_rows(appmod, raw_rows, plan_defs)

    @app.route('/api/customers/import/commit', methods=['POST'])
    @import_admin
    def customer_import_commit():
        try:
            _raw_rows, plan_defs, result = _parse_for_commit()
        except ImportFileError as e:
            return jsonify({'error': str(e)}), 400
        try:
            return jsonify(commit_import(appmod, result, plan_defs))
        except Exception as e:
            appmod.db.session.rollback()
            appmod.traceback.print_exc()
            return jsonify({'error': f'Import failed, nothing was saved: {e}'}), 400

    @app.route('/api/customers/import/error_report', methods=['POST'])
    @import_admin
    def customer_import_error_report():
        try:
            raw_rows, _plan_defs, result = _parse_for_commit()
        except ImportFileError as e:
            return jsonify({'error': str(e)}), 400
        return send_file(io.BytesIO(build_error_report(raw_rows, result)), mimetype=XLSX_MIME,
                         as_attachment=True, download_name='customer-import-skipped-rows.xlsx')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_customer_import.py -v`
Expected: all pass (31 tests).

- [ ] **Step 5: Run the neighbouring suites to check nothing regressed**

Run: `python -m pytest tests/test_gating.py tests/test_reseller_billing.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add customer_import.py tests/test_customer_import.py
git commit -m "feat(import): commit customer import and skipped-rows report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Frontend wizard

**Files:**
- Modify: `frontend/src/context/AppContext.js` (next to `addCustomer`, ~line 245)
- Create: `frontend/src/components/CustomerImportWizard.js`
- Create: `frontend/src/components/CustomerImportWizard.test.js`
- Modify: `frontend/src/components/SubscriptionsView.js` (header buttons ~line 1479; state near other `useState`s; dialog near the other Dialogs at the end of the returned JSX)

**Interfaces:**
- Consumes backend routes from Tasks 1–3 (responses exactly as specified there).
- Produces: `apiService.downloadImportTemplate()`, `apiService.validateCustomerImport(file)`, `apiService.commitCustomerImport(file, newPlans)`, `apiService.downloadImportErrorReport(file, newPlans)`; component `<CustomerImportWizard open onClose onImported />` (default export).

- [ ] **Step 1: Add the apiService methods**

In `frontend/src/context/AppContext.js`, directly after the `addCustomer:` line, add:

```javascript
    // Customer import wizard (docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md).
    downloadImportTemplate: () => api.get('/customers/import/template', { responseType: 'blob' }),
    validateCustomerImport: (file) => {
        const fd = new FormData();
        fd.append('file', file);
        return api.post('/customers/import/validate', fd);
    },
    commitCustomerImport: (file, newPlans) => {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('new_plans', JSON.stringify(newPlans || []));
        return api.post('/customers/import/commit', fd);
    },
    downloadImportErrorReport: (file, newPlans) => {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('new_plans', JSON.stringify(newPlans || []));
        return api.post('/customers/import/error_report', fd, { responseType: 'blob' });
    },
```

- [ ] **Step 2: Write the failing Jest test**

Create `frontend/src/components/CustomerImportWizard.test.js`:

```javascript
import React from 'react';
import { render, screen } from '@testing-library/react';
import CustomerImportWizard from './CustomerImportWizard';

jest.mock('../context/AppContext', () => ({
    useAppContext: () => ({ apiService: {} }),
}));

test('opens on the download-template step', () => {
    render(<CustomerImportWizard open onClose={() => {}} onImported={() => {}} />);
    expect(screen.getByText('Import customers')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /download template/i })).toBeInTheDocument();
});

test('renders nothing when closed', () => {
    render(<CustomerImportWizard open={false} onClose={() => {}} onImported={() => {}} />);
    expect(screen.queryByText('Import customers')).not.toBeInTheDocument();
});
```

Before writing it, open `frontend/src/components/SubscriptionsView.js` and check how it obtains `apiService` (search for `useAppContext`). If it is not `const { apiService } = useAppContext()`, adapt both the mock above and Step 4's component to the real shape.

- [ ] **Step 3: Run the test to verify it fails**

Run (from `frontend/`): `set CI=true&& npx react-scripts test --watchAll=false CustomerImportWizard` (PowerShell: `$env:CI='true'; npx react-scripts test --watchAll=false CustomerImportWizard`)
Expected: FAIL, "Cannot find module './CustomerImportWizard'".

- [ ] **Step 4: Create `frontend/src/components/CustomerImportWizard.js`**

```javascript
// Bulk customer import from the Excel template -- see
// docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md.
import React, { useMemo, useState } from 'react';
import {
    Alert, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
    DialogTitle, MenuItem, Stack, Step, StepLabel, Stepper, Table, TableBody, TableCell,
    TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import { useAppContext } from '../context/AppContext';

const STEPS = ['Download template', 'Upload file', 'Review', 'Done'];
const STATUS_COLOR = { ok: 'success', warning: 'warning', error: 'error' };

function saveBlob(blob, filename) {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
}

async function errorText(err, fallback) {
    const data = err?.response?.data;
    if (data instanceof Blob) {
        try { return JSON.parse(await data.text()).error || fallback; } catch (e) { return fallback; }
    }
    return data?.error || data?.msg || fallback;
}

export default function CustomerImportWizard({ open, onClose, onImported }) {
    const { apiService } = useAppContext();
    const [step, setStep] = useState(0);
    const [file, setFile] = useState(null);
    const [preview, setPreview] = useState(null);
    const [planDefs, setPlanDefs] = useState({});
    const [filter, setFilter] = useState('all');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const [result, setResult] = useState(null);

    const reset = () => {
        setStep(0); setFile(null); setPreview(null); setPlanDefs({});
        setFilter('all'); setBusy(false); setError(''); setResult(null);
    };
    const handleClose = () => { reset(); onClose(); };

    const newPlans = useMemo(() => (preview?.summary.unknown_plans || []).map((name) => ({
        name,
        price: planDefs[name]?.price ?? '',
        billing_cycle: planDefs[name]?.billing_cycle || 'monthly',
        currency: planDefs[name]?.currency || 'USD',
    })), [preview, planDefs]);
    const plansComplete = newPlans.every((p) => p.price !== '' && Number(p.price) >= 0);
    const setPlanField = (name, field, value) =>
        setPlanDefs((prev) => ({ ...prev, [name]: { ...prev[name], [field]: value } }));

    const handleDownloadTemplate = async () => {
        setError('');
        try {
            const res = await apiService.downloadImportTemplate();
            saveBlob(res.data, 'customer-import-template.xlsx');
            setStep(1);
        } catch (err) {
            setError(await errorText(err, 'Could not download the template.'));
        }
    };

    const handleValidate = async () => {
        if (!file) return;
        setBusy(true); setError('');
        try {
            const res = await apiService.validateCustomerImport(file);
            setPreview(res.data);
            setStep(2);
        } catch (err) {
            setError(await errorText(err, 'Could not read the file.'));
        } finally {
            setBusy(false);
        }
    };

    const handleCommit = async () => {
        setBusy(true); setError('');
        try {
            const payload = newPlans.map((p) => ({ ...p, price: Number(p.price) }));
            const res = await apiService.commitCustomerImport(file, payload);
            setResult(res.data);
            setStep(3);
            onImported();
        } catch (err) {
            setError(await errorText(err, 'Import failed. Nothing was saved.'));
        } finally {
            setBusy(false);
        }
    };

    const handleErrorReport = async () => {
        setError('');
        try {
            const payload = newPlans.map((p) => ({ ...p, price: Number(p.price) }));
            const res = await apiService.downloadImportErrorReport(file, payload);
            saveBlob(res.data, 'customer-import-skipped-rows.xlsx');
        } catch (err) {
            setError(await errorText(err, 'Could not build the report.'));
        }
    };

    const rows = (preview?.rows || []).filter((r) => filter === 'all' || r.status === filter);
    const summary = preview?.summary;

    return (
        <Dialog open={open} onClose={busy ? undefined : handleClose} maxWidth="lg" fullWidth>
            <DialogTitle>Import customers</DialogTitle>
            <DialogContent dividers>
                <Stepper activeStep={step} sx={{ mb: 3 }}>
                    {STEPS.map((label) => <Step key={label}><StepLabel>{label}</StepLabel></Step>)}
                </Stepper>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

                {step === 0 && (
                    <Stack spacing={2} alignItems="flex-start">
                        <Typography>
                            Download the Excel template, fill in one row per customer, then upload it.
                            Nothing is billed and no WhatsApp message is sent: each customer keeps the
                            expiry date you enter, and billing continues from that date.
                        </Typography>
                        <Button variant="contained" startIcon={<DownloadIcon />} onClick={handleDownloadTemplate}>
                            Download template
                        </Button>
                        <Button onClick={() => setStep(1)}>I already have a filled template</Button>
                    </Stack>
                )}

                {step === 1 && (
                    <Stack spacing={2} alignItems="flex-start">
                        <Button variant="outlined" component="label" startIcon={<UploadFileIcon />}>
                            {file ? file.name : 'Choose .xlsx file'}
                            <input hidden type="file" accept=".xlsx"
                                onChange={(e) => setFile(e.target.files?.[0] || null)} />
                        </Button>
                        <Typography variant="body2" color="text.secondary">Up to 5,000 customers, 5 MB.</Typography>
                    </Stack>
                )}

                {step === 2 && summary && (
                    <Stack spacing={2}>
                        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                            {[['all', `All ${summary.total}`], ['ok', `OK ${summary.ok}`],
                              ['warning', `Warnings ${summary.warning}`], ['error', `Errors ${summary.error}`]]
                                .map(([key, label]) => (
                                    <Chip key={key} label={label} color={STATUS_COLOR[key] || 'default'}
                                        variant={filter === key ? 'filled' : 'outlined'} onClick={() => setFilter(key)} />
                                ))}
                        </Stack>
                        <Alert severity={summary.importable ? 'info' : 'warning'}>
                            {summary.importable} of {summary.total} customers will be imported.
                            {summary.customer_limit != null &&
                                ` Your plan allows ${summary.customer_limit} customers (you have ${summary.existing_customers}).`}
                            {summary.new_sectors.length > 0 && ` New sectors: ${summary.new_sectors.join(', ')}.`}
                        </Alert>

                        {newPlans.length > 0 && (
                            <Box>
                                <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
                                    New plans. Set a price for each before importing.
                                </Typography>
                                {newPlans.map((p) => (
                                    <Stack key={p.name} direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 1 }}>
                                        <TextField label="Plan" value={p.name} size="small" disabled />
                                        <TextField label="Price" type="number" size="small" value={p.price}
                                            onChange={(e) => setPlanField(p.name, 'price', e.target.value)} />
                                        <TextField select label="Cycle" size="small" value={p.billing_cycle}
                                            onChange={(e) => setPlanField(p.name, 'billing_cycle', e.target.value)}>
                                            <MenuItem value="monthly">Monthly</MenuItem>
                                            <MenuItem value="yearly">Yearly</MenuItem>
                                        </TextField>
                                        <TextField select label="Currency" size="small" value={p.currency}
                                            onChange={(e) => setPlanField(p.name, 'currency', e.target.value)}>
                                            <MenuItem value="USD">USD</MenuItem>
                                            <MenuItem value="LBP">LBP</MenuItem>
                                        </TextField>
                                    </Stack>
                                ))}
                            </Box>
                        )}

                        <TableContainer sx={{ maxHeight: 420 }}>
                            <Table size="small" stickyHeader>
                                <TableHead>
                                    <TableRow>
                                        <TableCell>Row</TableCell><TableCell>Status</TableCell>
                                        <TableCell>Name</TableCell><TableCell>Phone</TableCell>
                                        <TableCell>Plan</TableCell><TableCell>Expiry</TableCell>
                                        <TableCell>Messages</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {rows.map((r) => (
                                        <TableRow key={r.row}>
                                            <TableCell>{r.row}</TableCell>
                                            <TableCell>
                                                <Chip size="small" label={r.import ? r.status : `${r.status} (skip)`}
                                                    color={STATUS_COLOR[r.status]} />
                                            </TableCell>
                                            <TableCell>{r.data.name}</TableCell>
                                            <TableCell>{r.data.phone}</TableCell>
                                            <TableCell>{r.data.plan}</TableCell>
                                            <TableCell>{r.data.expiry_date}</TableCell>
                                            <TableCell>{r.messages.join(' · ')}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    </Stack>
                )}

                {step === 3 && result && (
                    <Stack spacing={2} alignItems="flex-start">
                        <Alert severity="success">
                            Imported {result.imported} customers
                            {result.plans_created > 0 && `, created ${result.plans_created} plans`}
                            {result.sectors_created > 0 && `, created ${result.sectors_created} sectors`}.
                        </Alert>
                        {result.skipped > 0 && (
                            <>
                                <Typography>{result.skipped} rows were skipped. Download them, fix them, and upload again.</Typography>
                                <Button variant="outlined" startIcon={<DownloadIcon />} onClick={handleErrorReport}>
                                    Download skipped rows
                                </Button>
                            </>
                        )}
                    </Stack>
                )}
            </DialogContent>
            <DialogActions>
                {step === 1 && <Button onClick={() => setStep(0)}>Back</Button>}
                {step === 2 && <Button onClick={() => { setStep(1); setPreview(null); }}>Back</Button>}
                <Button onClick={handleClose} disabled={busy}>{step === 3 ? 'Close' : 'Cancel'}</Button>
                {step === 1 && (
                    <Button variant="contained" onClick={handleValidate} disabled={!file || busy}>
                        {busy ? <CircularProgress size={20} /> : 'Check file'}
                    </Button>
                )}
                {step === 2 && (
                    <Button variant="contained" onClick={handleCommit}
                        disabled={busy || !summary?.importable || !plansComplete}>
                        {busy ? <CircularProgress size={20} /> : `Import ${summary?.importable || 0} customers`}
                    </Button>
                )}
            </DialogActions>
        </Dialog>
    );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run (from `frontend/`, PowerShell): `$env:CI='true'; npx react-scripts test --watchAll=false CustomerImportWizard`
Expected: 2 passed.

- [ ] **Step 6: Mount it in SubscriptionsView**

In `frontend/src/components/SubscriptionsView.js`:

1. Add the import next to the other component imports at the top:
   ```javascript
   import CustomerImportWizard from './CustomerImportWizard';
   ```
   and add `UploadFile as UploadFileIcon` to the `@mui/icons-material` import list (or `import UploadFileIcon from '@mui/icons-material/UploadFile';` if icons are imported one per line — match the file's style).
2. Next to `const canManageSubscriptions = ...` (~line 504) add:
   ```javascript
   const isAdmin = userRoles.includes('admin');
   const [importOpen, setImportOpen] = useState(false);
   ```
   (Put the `useState` with the component's other `useState` calls if the file keeps hooks grouped at the top; it must stay above any early `return`.)
3. Directly after the Export `<Button ...>` at ~line 1479 (the one with `onClick={handleExportCSV}` and its closing `</Button>}`), add a sibling button with the same `sx` as that Export button:
   ```javascript
   {isAdmin && <Button variant="contained" startIcon={<UploadFileIcon />} onClick={() => setImportOpen(true)} sx={/* copy the Export button's sx object exactly */}>
       Import
   </Button>}
   ```
4. Just before the component's final closing wrapper tag in its returned JSX (next to the other `<Dialog>`s), add:
   ```javascript
   <CustomerImportWizard open={importOpen} onClose={() => setImportOpen(false)} onImported={() => fetchCustomers()} />
   ```
   Replace `fetchCustomers()` with whatever function this component already calls to reload the list after `handleAddCustomer` succeeds (look inside `handleAddCustomer`, ~line 1330, and reuse that exact call).

- [ ] **Step 7: Build to make sure it compiles**

Run (from `frontend/`): `npm run build`
Expected: "Compiled successfully" (warnings acceptable only if they pre-exist; no new warnings from the two touched files). Then `git checkout -- build` or equivalent if the build output directory is tracked and changed — check `git status` and do NOT commit build output.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/context/AppContext.js frontend/src/components/CustomerImportWizard.js frontend/src/components/CustomerImportWizard.test.js frontend/src/components/SubscriptionsView.js
git commit -m "feat(import): customer import wizard UI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
