"""Bulk customer import from an .xlsx template -- see
docs/superpowers/specs/2026-09-30-customer-import-wizard-design.md.

Migration mode: customers arrive with their current paid-through expiry and
nothing is billed, linked to Whish, or sent on WhatsApp. Registered from
app.py via register_customer_import_routes(app, appmod) so app.py doesn't
grow further."""
import io
import json
import math
from datetime import date, datetime
from functools import wraps

from dateutil.relativedelta import relativedelta
from flask import jsonify, request, send_file
from flask_jwt_extended import verify_jwt_in_request
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

import plans
from tenancy import current_tenant, current_tenant_id, new_for_tenant, tenant_query

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

    @app.route('/api/customers/import/validate', methods=['POST'])
    @import_admin
    def customer_import_validate():
        try:
            result = validate_rows(appmod, read_workbook(request.files.get('file')))
        except ImportFileError as e:
            return jsonify({'error': str(e)}), 400
        return jsonify({'rows': public_rows(result), 'summary': result['summary']})

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
