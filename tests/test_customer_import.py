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


def test_read_workbook_ignores_stray_far_cells_quickly():
    import time
    wb = Workbook()
    ws = wb.active
    ws.title = 'Customers'
    ws.append(ci.HEADERS)
    ws.append([_row().get(h) for h in ci.HEADERS])
    ws.cell(row=300000, column=1, value='stray')
    ws.cell(row=2, column=15000, value='far right')
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    started = time.monotonic()
    rows = ci.read_workbook(_Upload(buf))
    assert time.monotonic() - started < 10
    assert len(rows) == 1 and rows[0]['name'] == 'Ali'


def test_validate_rejects_oversized_upload(client):
    h = make_tenant(client, 'Biz', 'v_big')
    r = client.post('/api/customers/import/validate', headers=h,
                    data={'file': (io.BytesIO(b'0' * (7 * 1024 * 1024)), 'c.xlsx')},
                    content_type='multipart/form-data')
    assert r.status_code == 400
    assert '5 MB' in r.get_json()['error']


def test_validate_rejects_overlong_usernames(client):
    h = make_tenant(client, 'Biz', 'v_long')
    _plan(client, h)
    body = _validate(client, h, [_row(upstream_username='u' * 101)]).get_json()
    assert body['rows'][0]['status'] == 'error'
    assert 'upstream_username is longer than 100' in ' '.join(body['rows'][0]['messages'])


def test_reupload_of_imported_row_with_mac_is_duplicate_not_error(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_reup_mac')
    _plan(client, h)
    rows = [_row(name='A', phone='1', cpe_mac='AA:BB:CC:DD:EE:02')]
    assert _commit(client, h, rows).get_json()['imported'] == 1
    body = _validate(client, h, rows).get_json()
    assert body['rows'][0]['status'] == 'warning'
    assert body['rows'][0]['import'] is False


def test_validate_expiry_range(client):
    h = make_tenant(client, 'Biz', 'v_range')
    _plan(client, h, 'Basic', price=20)
    two_years_ago = (datetime.utcnow() - timedelta(days=730)).strftime('%Y-%m-%d')
    ten_days_ago = (datetime.utcnow() - timedelta(days=10)).strftime('%Y-%m-%d')
    body = _validate(client, h, [
        _row(name='A', phone='1', expiry_date=two_years_ago),
        _row(name='B', phone='2', expiry_date=two_years_ago, active='no'),
        _row(name='C', phone='3', expiry_date='2099-01-01'),
        _row(name='D', phone='4', expiry_date=ten_days_ago),
    ]).get_json()
    assert [r['status'] for r in body['rows']] == ['error', 'ok', 'error', 'warning']
    assert '1 billing cycle(s) (20 total)' in ' '.join(body['rows'][3]['messages'])


def test_plan_only_on_error_row_is_not_listed(client):
    h = make_tenant(client, 'Biz', 'v_planlist')
    _plan(client, h)
    body = _validate(client, h, [_row(name='', plan='Ghost', sector='Nowhere'), _row()]).get_json()
    assert body['summary']['unknown_plans'] == []
    assert body['summary']['new_sectors'] == []


def test_commit_returns_report_of_skipped_rows_only(client, no_whatsapp):
    import base64
    h = make_tenant(client, 'Biz', 'c_report')
    _plan(client, h)
    body = _commit(client, h, [_row(name='Good', phone='1'),
                               _row(name='', phone='2', notes='bad row')]).get_json()
    assert body['imported'] == 1 and body['skipped'] == 1
    ws = load_workbook(io.BytesIO(base64.b64decode(body['skipped_report'])))['Customers']
    header = [c.value for c in ws[1]]
    assert header == ci.HEADERS + ['errors']
    data_rows = list(ws.iter_rows(min_row=2))
    assert len(data_rows) == 1
    assert data_rows[0][header.index('phone')].value == '2'
    assert 'name is required' in data_rows[0][-1].value


def test_error_report_keeps_equals_values_as_text():
    # Called directly: an uploaded formula cell has no cached value, so the
    # reader (data_only=True) would see it as blank before it got here.
    raw_rows = [{'_row': 2, 'name': '', 'phone': '2', 'notes': '=HYPERLINK("x")'}]
    result = {'rows': [{'row': 2, 'import': False, 'messages': ['name is required.']}]}
    ws = load_workbook(io.BytesIO(ci.build_error_report(raw_rows, result)))['Customers']
    cell = ws.cell(row=2, column=ci.HEADERS.index('notes') + 1)
    assert cell.value == '=HYPERLINK("x")' and cell.data_type == 's'


def test_commit_without_skips_has_no_report(client, no_whatsapp):
    h = make_tenant(client, 'Biz', 'c_noreport')
    _plan(client, h)
    body = _commit(client, h, [_row()]).get_json()
    assert body['skipped'] == 0 and 'skipped_report' not in body
