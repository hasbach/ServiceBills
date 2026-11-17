"""Books (double-entry ledger): pure mapping of financial records to journal lines.

No database access and no import of app.py. The flush listener in app.py calls
`desired_lines` for a record's current state, `diff_lines` against what is
already posted, and writes the difference. Spec:
docs/superpowers/specs/2026-11-17-ledger-design.md

A `Line` amount is signed: positive = debit, negative = credit.
"""
from collections import namedtuple
from datetime import date, datetime

Line = namedtuple('Line', 'account_key party_type party_id amount day')

# (key, number, name, type, party_type)
CHART = [
    ('cash', 1000, 'Cash', 'asset', None),
    ('whish', 1010, 'Whish', 'asset', None),
    ('customers', 1100, 'Customers owing', 'asset', 'customer'),
    ('resellers', 1110, 'Resellers owing', 'asset', 'reseller'),
    ('upstream', 1200, 'Upstream provider credit', 'asset', 'upstream'),
    ('suppliers', 2000, 'Suppliers owed', 'liability', 'supplier'),
    ('salaries', 2100, 'Salaries owed', 'liability', 'employee'),
    ('opening', 3000, 'Opening balances', 'equity', None),
    ('money_in', 3100, 'Money put in (unclassified)', 'equity', None),
    ('corrections', 3900, 'Balance corrections', 'equity', None),
    ('income_subscriptions', 4000, 'Subscription income', 'income', None),
    ('income_resellers', 4100, 'Reseller income', 'income', None),
    ('cost_upstream', 5000, 'Upstream cost', 'expense', None),
    ('cost_payroll', 5100, 'Payroll', 'expense', None),
    ('discounts', 5200, 'Discounts and write-offs', 'expense', None),
    ('over_short', 5900, 'Cash over/short', 'expense', None),
]
_TYPE_BY_KEY = {c[0]: c[3] for c in CHART}
PROFIT_TYPES = {'income', 'expense'}

# party type -> the per-party account that carries it
PARTY_ACCOUNT = {
    'customer': 'customers', 'reseller': 'resellers', 'upstream': 'upstream',
    'supplier': 'suppliers', 'employee': 'salaries',
}

_EPS = 0.00005


def account_type(key):
    if key.startswith('expense:'):
        return 'expense'
    return _TYPE_BY_KEY[key]


def money_account(via):
    return 'whish' if via in ('whish', 'whish_transfer') else 'cash'


def _amt(value):
    return round(float(value or 0), 4)


def _day(value, local_day):
    """Business-local day of a datetime; a plain date is used as is."""
    if isinstance(value, datetime):
        return local_day(value, False)
    return value


def _pair(dr_key, dr_pt, dr_pid, cr_key, cr_pt, cr_pid, amount, day):
    """Dr dr / Cr cr, amount > 0. Empty when the amount is 0."""
    amount = _amt(amount)
    if not amount:
        return []
    return [Line(dr_key, dr_pt, dr_pid, amount, day),
            Line(cr_key, cr_pt, cr_pid, -amount, day)]


def _not_reverted(p):
    """Mirror of app._payment_not_reverted: reverted_at stays as the audit trail
    of the last revert; a later collected_at/paid_at means it was paid again."""
    if p.reverted_at is None:
        return True
    when = p.collected_at or p.paid_at
    return when is not None and p.reverted_at < when


def _payment_lines(p, local_day):
    cust = ('customer', p.customer_id)
    amount = _amt(p.amount)
    lines = []
    money = money_account(p.collected_via)
    receipt_when = p.collected_at or p.paid_at
    bill_day = local_day(p.date, True)
    receipt_day = local_day(receipt_when, False) if receipt_when else bill_day
    live = bool(p.paid) and _not_reverted(p)

    if p.pre_payment:
        if live:
            lines += _pair(money, None, None, 'customers', *cust, amount, receipt_day)
        return lines
    if p.is_refund:
        if live:
            lines += _pair('customers', *cust, money, None, None, abs(amount), receipt_day)
        return lines

    lines += _pair('customers', *cust, 'income_subscriptions', None, None, amount, bill_day)
    if p.is_gratis:
        if _not_reverted(p):
            lines += _pair('discounts', None, None, 'customers', *cust, amount,
                           receipt_day)
    elif live and not p.settled_from_credit:
        lines += _pair(money, None, None, 'customers', *cust, amount, receipt_day)
    return lines


def _reseller_lines(r, local_day):
    day = _day(r.date, local_day)
    who = ('reseller', r.reseller_id)
    if r.type == 'credit_added':
        return _pair('resellers', *who, 'income_resellers', None, None, r.amount, day)
    if r.type == 'discount_applied':
        return _pair('discounts', None, None, 'resellers', *who, r.amount, day)
    if r.type in ('payment_received', 'payment_collected'):
        return _pair(money_account(r.paid_via), None, None, 'resellers', *who, r.amount, day)
    return []


def _upstream_lines(u, local_day):
    day = _day(u.date, local_day)
    who = ('upstream', u.upstream_provider_id)
    if u.type == 'balance_topup':
        return _pair('upstream', *who, money_account(u.paid_via), None, None, u.amount, day)
    if u.type == 'renewal_cost':
        return _pair('cost_upstream', None, None, 'upstream', *who, u.amount, day)
    return []


def _supplier_lines(s, local_day):
    day = _day(s.payment_date, local_day)
    return _pair('suppliers', 'supplier', s.supplier_id,
                 money_account(s.paid_via), None, None, s.amount, day)


def _expense_lines(e, local_day):
    day = _day(e.date, local_day)
    key = 'expense:%s' % e.category_id if e.category_id else 'expense:none'
    if e.is_credit and e.supplier_id:
        return _pair(key, None, None, 'suppliers', 'supplier', e.supplier_id, e.amount, day)
    if e.employee_id:
        return _pair('salaries', 'employee', e.employee_id,
                     money_account(e.paid_via), None, None, e.amount, day)
    return _pair(key, None, None, money_account(e.paid_via), None, None, e.amount, day)


def _salary_charge_lines(c, local_day):
    day = _day(c.date, local_day)
    who = ('employee', c.employee_id)
    if c.type in ('salary', 'bonus'):
        return _pair('cost_payroll', None, None, 'salaries', *who, c.amount, day)
    if c.type == 'deduction':
        return _pair('salaries', *who, 'cost_payroll', None, None, c.amount, day)
    return []


def _cash_entry_lines(c, local_day):
    return _pair(money_account(c.account), None, None, 'money_in', None, None,
                 c.amount, _day(c.date, local_day))


def _transfer_lines(t, local_day):
    return _pair(money_account(t.to_account), None, None,
                 money_account(t.from_account), None, None, t.amount,
                 _day(t.date, local_day))


def _day_close_lines(d, local_day):
    lines = []
    for channel, expected, counted in (('cash', d.expected_cash, d.counted_cash),
                                       ('whish', d.expected_whish, d.counted_whish)):
        if counted is None:
            continue
        diff = _amt(_amt(counted) - _amt(expected))
        if diff:
            lines += [Line(channel, None, None, diff, d.day),
                      Line('over_short', None, None, -diff, d.day)]
    return lines


_MAPPERS = {
    'payment': _payment_lines,
    'reseller_payment': _reseller_lines,
    'upstream_payment': _upstream_lines,
    'supplier_payment': _supplier_lines,
    'expense': _expense_lines,
    'salary_charge': _salary_charge_lines,
    'salary_payment': lambda rec, local_day: [],
    'cash_entry': _cash_entry_lines,
    'account_transfer': _transfer_lines,
    'day_close': _day_close_lines,
}


def desired_lines(kind, rec, local_day):
    """The lines `rec` should have contributed to the books in its current state."""
    if getattr(rec, '_deleted', False):
        return []
    return _MAPPERS[kind](rec, local_day)


def diff_lines(desired, already, today, profit_to_opening):
    """Lines to post so that `already` becomes `desired`.

    Netting is keyed by (account, party type, party id, day). Dating: a key
    absent from `already` is a genuinely new event and keeps its natural day;
    a key already posted whose amount changes (grows or shrinks) or that is
    removed is dated `today` (corrections are dated the day they happen). The
    output must balance per day, because the caller books one entry per day:
    if any day's lines do not sum to 0 (e.g. a method change posts the new
    side on its natural day and takes the old side out today), all output
    lines are re-dated to `today`.

    With `profit_to_opening`, income/expense lines for an account the record
    already posted to go to 'opening' (prior-period adjustment). Only accounts
    already posted are redirected, so a gratis made today on an old bill stays
    a current discount, and recategorising a pre-books expense posts the new
    category in the current period (the old category's reversal goes to
    opening)."""
    def net(lines):
        out = {}
        for l in lines:
            k = (l.account_key, l.party_type, l.party_id, l.day)
            out[k] = out.get(k, 0.0) + l.amount
        return out

    want, have = net(desired), net(already)
    posted_accounts = {l.account_key for l in already}
    desired_groups = {k[:3] for k in want}
    merged = {}
    for k in list(want) + [k for k in have if k not in want]:
        delta = want.get(k, 0.0) - have.get(k, 0.0)
        if abs(delta) < _EPS:
            continue
        key, ptype, pid, day = k
        if k in have:
            day = today
        if (profit_to_opening and key in posted_accounts
                and account_type(key) in PROFIT_TYPES):
            key, ptype, pid = 'opening', None, None
        mk = (key, ptype, pid, day)
        merged[mk] = merged.get(mk, 0.0) + delta
    out = [Line(k[0], k[1], k[2], round(v, 4), k[3])
           for k, v in merged.items() if abs(v) >= _EPS]
    per_day = {}
    for l in out:
        per_day[l.day] = per_day.get(l.day, 0.0) + l.amount
    if any(abs(v) >= _EPS for v in per_day.values()):
        remerged = {}
        for l in out:
            k = (l.account_key, l.party_type, l.party_id)
            remerged[k] = remerged.get(k, 0.0) + l.amount
        out = [Line(k[0], k[1], k[2], round(v, 4), today)
               for k, v in remerged.items() if abs(v) >= _EPS]
    return out


def party_balance_from_books(party_type, debit_minus_credit):
    """The stored-balance convention for a party from its books total (Dr - Cr)."""
    if party_type in ('reseller', 'upstream'):
        return debit_minus_credit
    return -debit_minus_credit


def balance_delta_to_lines(party_type, party_id, delta_balance, day):
    """Lines for a stored-balance change with no record behind it."""
    delta = _amt(delta_balance)
    if not delta:
        return []
    dc = _amt(party_balance_from_books(party_type, delta))  # sign flip is its own inverse
    return [Line(PARTY_ACCOUNT[party_type], party_type, party_id, dc, day),
            Line('corrections', None, None, -dc, day)]
