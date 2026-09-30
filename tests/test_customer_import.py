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
