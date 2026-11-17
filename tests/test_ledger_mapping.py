"""Pure unit tests for the books' mapping module (ledger.py). No app, no DB."""
from datetime import date, datetime
from types import SimpleNamespace as NS

from ledger import (Line, CHART, PROFIT_TYPES, account_type, desired_lines,
                    money_account, diff_lines, party_balance_from_books,
                    balance_delta_to_lines)


def local_day(dt, calendar):
    if isinstance(dt, datetime):
        return dt.date()
    return dt


D1 = date(2026, 11, 3)
D5 = date(2026, 11, 5)
D6 = date(2026, 11, 6)
T5 = datetime(2026, 11, 5, 10, 0)
T6 = datetime(2026, 11, 6, 9, 0)
TODAY = date(2026, 11, 17)


def L(key, ptype, pid, amount, day):
    return Line(key, ptype, pid, amount, day)


def check(lines):
    assert round(sum(l.amount for l in lines), 6) == 0
    per_day = {}
    for l in lines:
        per_day[l.day] = per_day.get(l.day, 0) + l.amount
    assert all(abs(v) < 0.00005 for v in per_day.values()), per_day
    return lines


def pay(**kw):
    base = dict(amount=25, date=D1, paid=False, paid_at=None, collected_at=None,
                pre_payment=False, is_gratis=False, reverted_at=None,
                is_refund=False, collected_via=None, settled_from_credit=False,
                customer_id=7, _deleted=False)
    base.update(kw)
    return NS(**base)


def lines_of(kind, rec):
    return check(desired_lines(kind, rec, local_day))


BILL = [L('customers', 'customer', 7, 25, D1),
        L('income_subscriptions', None, None, -25, D1)]


# ---- bills -----------------------------------------------------------------
def test_unpaid_bill():
    assert lines_of('payment', pay()) == BILL


def test_paid_bill_cash():
    got = lines_of('payment', pay(paid=True, paid_at=T5))
    assert got == BILL + [L('cash', None, None, 25, D5),
                          L('customers', 'customer', 7, -25, D5)]


def test_paid_bill_whish_transfer():
    got = lines_of('payment', pay(paid=True, paid_at=T5, collected_at=T6,
                                  collected_via='whish_transfer'))
    assert got == BILL + [L('whish', None, None, 25, D6),
                          L('customers', 'customer', 7, -25, D6)]


def test_bill_settled_from_credit_has_no_money_line():
    got = lines_of('payment', pay(paid=True, paid_at=T5, settled_from_credit=True))
    assert got == BILL


def test_gratis_bill():
    got = lines_of('payment', pay(paid=True, paid_at=T5, is_gratis=True))
    assert got == BILL + [L('discounts', None, None, 25, D5),
                          L('customers', 'customer', 7, -25, D5)]


# ---- customer money rows ---------------------------------------------------
def test_prepayment():
    got = lines_of('payment', pay(pre_payment=True, paid=True, paid_at=T5,
                                  collected_via='whish'))
    assert got == [L('whish', None, None, 25, D5),
                   L('customers', 'customer', 7, -25, D5)]


def test_refund():
    got = lines_of('payment', pay(is_refund=True, paid=True, paid_at=T5))
    assert got == [L('customers', 'customer', 7, 25, D5),
                   L('cash', None, None, -25, D5)]


def test_reverted_payment_keeps_only_bill_lines():
    got = lines_of('payment', pay(paid=True, paid_at=T5, reverted_at=T6))
    assert got == BILL
    # reverted prepayment / refund: nothing at all
    assert lines_of('payment', pay(pre_payment=True, paid=True, paid_at=T5,
                                   reverted_at=T6)) == []
    assert lines_of('payment', pay(is_refund=True, paid=True, paid_at=T5,
                                   reverted_at=T6)) == []


def test_repaid_after_revert_counts_again():
    got = lines_of('payment', pay(paid=True, paid_at=T5, collected_at=T6,
                                  reverted_at=datetime(2026, 11, 5, 12, 0)))
    assert got == BILL + [L('cash', None, None, 25, D6),
                          L('customers', 'customer', 7, -25, D6)]


def test_deleted_record_has_no_lines():
    assert desired_lines('payment', pay(_deleted=True), local_day) == []
    rec = NS(account='cash', amount=5, date=D1, _deleted=True)
    assert desired_lines('cash_entry', rec, local_day) == []


# ---- reseller rows ---------------------------------------------------------
def rpay(type_, **kw):
    base = dict(reseller_id=4, amount=40, type=type_, date=D5, paid_via=None,
                _deleted=False)
    base.update(kw)
    return NS(**base)


def test_reseller_credit_added():
    assert lines_of('reseller_payment', rpay('credit_added')) == [
        L('resellers', 'reseller', 4, 40, D5),
        L('income_resellers', None, None, -40, D5)]


def test_reseller_discount():
    assert lines_of('reseller_payment', rpay('discount_applied')) == [
        L('discounts', None, None, 40, D5),
        L('resellers', 'reseller', 4, -40, D5)]


def test_reseller_payment_received_whish():
    assert lines_of('reseller_payment', rpay('payment_received', paid_via='whish')) == [
        L('whish', None, None, 40, D5),
        L('resellers', 'reseller', 4, -40, D5)]


def test_reseller_payment_collected_legacy():
    assert lines_of('reseller_payment', rpay('payment_collected')) == [
        L('cash', None, None, 40, D5),
        L('resellers', 'reseller', 4, -40, D5)]


# ---- upstream rows ---------------------------------------------------------
def upay(type_, **kw):
    base = dict(upstream_provider_id=2, amount=300, type=type_, date=D5,
                paid_via=None, _deleted=False)
    base.update(kw)
    return NS(**base)


def test_upstream_topup():
    assert lines_of('upstream_payment', upay('balance_topup', paid_via='whish')) == [
        L('upstream', 'upstream', 2, 300, D5),
        L('whish', None, None, -300, D5)]


def test_upstream_renewal_cost():
    assert lines_of('upstream_payment', upay('renewal_cost')) == [
        L('cost_upstream', None, None, 300, D5),
        L('upstream', 'upstream', 2, -300, D5)]


# ---- suppliers and expenses ------------------------------------------------
def test_supplier_payment():
    rec = NS(supplier_id=9, amount=80, payment_date=D5, paid_via=None, _deleted=False)
    assert lines_of('supplier_payment', rec) == [
        L('suppliers', 'supplier', 9, 80, D5),
        L('cash', None, None, -80, D5)]


def exp(**kw):
    base = dict(amount=60, date=D5, supplier_id=None, employee_id=None,
                is_credit=False, paid_via=None, category_id=3,
                category_name='Fuel', _deleted=False)
    base.update(kw)
    return NS(**base)


def test_expense_on_supplier_credit():
    assert lines_of('expense', exp(is_credit=True, supplier_id=9)) == [
        L('expense:3', None, None, 60, D5),
        L('suppliers', 'supplier', 9, -60, D5)]


def test_expense_payroll_payment():
    assert lines_of('expense', exp(employee_id=5, paid_via='whish')) == [
        L('salaries', 'employee', 5, 60, D5),
        L('whish', None, None, -60, D5)]


def test_plain_expense_category_account():
    assert lines_of('expense', exp()) == [
        L('expense:3', None, None, 60, D5),
        L('cash', None, None, -60, D5)]
    assert lines_of('expense', exp(category_id=None))[0].account_key == 'expense:none'


# ---- payroll ---------------------------------------------------------------
def test_salary_charge():
    rec = NS(employee_id=5, type='bonus', amount=100, date=D5, _deleted=False)
    assert lines_of('salary_charge', rec) == [
        L('cost_payroll', None, None, 100, D5),
        L('salaries', 'employee', 5, -100, D5)]


def test_salary_deduction():
    rec = NS(employee_id=5, type='deduction', amount=30, date=D5, _deleted=False)
    assert lines_of('salary_charge', rec) == [
        L('salaries', 'employee', 5, 30, D5),
        L('cost_payroll', None, None, -30, D5)]


def test_salary_payment_legacy_has_no_lines():
    assert desired_lines('salary_payment', NS(amount=10, _deleted=False), local_day) == []


# ---- cash entries, transfers, day close ------------------------------------
def test_cash_entry():
    rec = NS(account='whish', amount=70, date=D5, _deleted=False)
    assert lines_of('cash_entry', rec) == [
        L('whish', None, None, 70, D5),
        L('money_in', None, None, -70, D5)]


def test_account_transfer():
    rec = NS(from_account='cash', to_account='whish', amount=15, date=D5, _deleted=False)
    assert lines_of('account_transfer', rec) == [
        L('whish', None, None, 15, D5),
        L('cash', None, None, -15, D5)]


def test_day_close_shortfall_and_surplus():
    rec = NS(day=D5, expected_cash=100, counted_cash=90,
             expected_whish=50, counted_whish=55, _deleted=False)
    assert lines_of('day_close', rec) == [
        L('cash', None, None, -10, D5), L('over_short', None, None, 10, D5),
        L('whish', None, None, 5, D5), L('over_short', None, None, -5, D5)]
    # None counted = no diff; equal = no lines
    rec2 = NS(day=D5, expected_cash=100, counted_cash=None,
              expected_whish=50, counted_whish=50, _deleted=False)
    assert lines_of('day_close', rec2) == []


# ---- diff_lines ------------------------------------------------------------
def test_diff_new_record_keeps_natural_days():
    desired = desired_lines('payment', pay(paid=True, paid_at=T5), local_day)
    assert diff_lines(desired, [], TODAY, False) == desired


def test_diff_removal_dated_today():
    already = desired_lines('payment', pay(paid=True, paid_at=T5), local_day)
    got = check(diff_lines([], already, TODAY, False))
    assert {l.day for l in got} == {TODAY}
    # revert: only the receipt lines come out, net of the bill lines
    got = check(diff_lines(BILL, already, TODAY, False))
    assert sorted((l.account_key, l.amount, l.day) for l in got) == [
        ('cash', -25, TODAY), ('customers', 25, TODAY)]
    # growth of an existing line is a correction dated today
    bigger = [L('customers', 'customer', 7, 35, D1), L('income_subscriptions', None, None, -35, D1)]
    got = check(diff_lines(bigger, BILL, TODAY, False))
    assert sorted((l.account_key, l.amount, l.day) for l in got) == [
        ('customers', 10, TODAY), ('income_subscriptions', -10, TODAY)]


def test_diff_profit_to_opening_only_for_accounts_already_posted():
    # delete a bill that was already posted: its income reversal goes to opening
    got = check(diff_lines([], BILL, TODAY, True))
    assert sorted((l.account_key, l.amount) for l in got) == [
        ('customers', -25), ('opening', 25)]
    # a profit account not yet posted for this record stays as is
    desired = BILL + [L('discounts', None, None, 25, D5),
                      L('customers', 'customer', 7, -25, D5)]
    got = check(diff_lines(desired, BILL, TODAY, True))
    assert 'discounts' in {l.account_key for l in got}
    assert 'opening' not in {l.account_key for l in got}
    # flag off: profit side stays put
    got = diff_lines([], BILL, TODAY, False)
    assert 'income_subscriptions' in {l.account_key for l in got}


def test_diff_ignores_tiny_noise():
    a = [L('cash', None, None, 10.00001, D1), L('money_in', None, None, -10.00001, D1)]
    b = [L('cash', None, None, 10, D1), L('money_in', None, None, -10, D1)]
    assert diff_lines(a, b, TODAY, False) == []


# ---- party balances and chart ----------------------------------------------
def test_party_balance_signs():
    assert party_balance_from_books('customer', 10) == -10
    assert party_balance_from_books('reseller', 10) == 10
    assert party_balance_from_books('upstream', 10) == 10
    assert party_balance_from_books('supplier', 10) == -10
    assert party_balance_from_books('employee', 10) == -10


def test_balance_delta_to_lines_round_trips():
    for ptype in ('customer', 'reseller', 'upstream', 'supplier', 'employee'):
        got = check(balance_delta_to_lines(ptype, 3, 12.5, D5))
        party = [l for l in got if l.party_type == ptype]
        assert len(party) == 1 and party[0].party_id == 3
        assert party_balance_from_books(ptype, party[0].amount) == 12.5
        assert [l.account_key for l in got if l.party_type is None] == ['corrections']
    assert balance_delta_to_lines('customer', 3, 0, D5) == []


def test_chart_and_account_types():
    keys = [c[0] for c in CHART]
    assert len(keys) == len(set(keys)) == 16
    assert PROFIT_TYPES == {'income', 'expense'}
    assert account_type('expense:12') == 'expense'
    assert account_type('cash') == 'asset' and account_type('suppliers') == 'liability'
    assert account_type('opening') == 'equity' and account_type('income_resellers') == 'income'
    assert money_account('whish_transfer') == 'whish' and money_account(None) == 'cash'


PAID = lambda **kw: desired_lines('payment', pay(paid=True, paid_at=T5, **kw), local_day)


def test_diff_method_change_cash_to_whish_balances_per_day_dated_today():
    already = PAID()
    desired = PAID(collected_via='whish')
    got = check(diff_lines(desired, already, TODAY, False))
    assert sorted((l.account_key, l.amount, l.day) for l in got) == [
        ('cash', -25, TODAY), ('whish', 25, TODAY)]


def test_diff_expense_recategorised_balances_per_day():
    already = desired_lines('expense', exp(category_id=3), local_day)
    desired = desired_lines('expense', exp(category_id=4), local_day)
    got = check(diff_lines(desired, already, TODAY, False))
    assert sorted((l.account_key, l.amount, l.day) for l in got) == [
        ('expense:3', -60, TODAY), ('expense:4', 60, TODAY)]


def test_diff_amount_increase_on_existing_record_dated_today():
    already = PAID()
    desired = desired_lines('payment', pay(amount=30, paid=True, paid_at=T5), local_day)
    got = check(diff_lines(desired, already, TODAY, False))
    assert got and {l.day for l in got} == {TODAY}
    # customers +5 (bill day) and -5 (receipt day) net out once both are dated today
    assert sorted((l.account_key, l.amount) for l in got) == [
        ('cash', 5), ('income_subscriptions', -5)]


def test_diff_payment_arriving_keeps_receipt_day():
    got = check(diff_lines(PAID(), BILL, TODAY, False))
    assert sorted((l.account_key, l.amount, l.day) for l in got) == [
        ('cash', 25, D5), ('customers', -25, D5)]


def test_paid_without_timestamps_falls_back_to_bill_day():
    got = lines_of('payment', pay(paid=True))
    assert got == BILL + [L('cash', None, None, 25, D1),
                          L('customers', 'customer', 7, -25, D1)]


def test_unpaid_gratis_lines():
    got = lines_of('payment', pay(is_gratis=True))
    assert got == BILL + [L('discounts', None, None, 25, D1),
                          L('customers', 'customer', 7, -25, D1)]
