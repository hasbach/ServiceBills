import { validateItem, insertAt, countPlaceholders } from './standardTemplateValidation';

const base = {
    key: 'payment_paid',
    name: 'payment_confirmation_v2',
    header: '',
    body: 'Dear {customer_name}, we received {amount}. Balance: {balance}.',
    footer: '',
    buttons: [],
    use_existing: false,
    existing: null,
    variables: [{ key: 'customer_name' }, { key: 'amount' }, { key: 'balance' }],
};

test('valid catalog text passes', () => {
    expect(validateItem(base).errors).toEqual([]);
});

test('missing variable is named', () => {
    const r = validateItem({ ...base, body: 'Dear {customer_name}, we received {amount}. Thanks.' });
    expect(r.errors).toContain('Missing variable {balance}');
});

test('unknown and duplicate variables are named', () => {
    const r = validateItem({ ...base, body: 'Hi {customer_name} {amount} {balance} {balance} {foo} {Bar}.' });
    expect(r.errors).toEqual(expect.arrayContaining([
        'Variable {balance} must appear exactly once (found 2)',
        'Unknown variable {foo}: this template can only use {customer_name}, {amount}, {balance}',
        'Unknown variable {Bar}',
    ]));
});

test('variables may move unless the order is fixed', () => {
    const moved = { ...base, body: 'Balance {balance}, paid {amount}, thanks {customer_name}!' };
    expect(validateItem(moved).errors).toEqual([]);
    expect(validateItem({ ...moved, fixed_order: true }).errors[0]).toMatch(/must stay in this order/);
});

test('body may not start or end with a variable', () => {
    const r = validateItem({ ...base, body: '{customer_name} paid {amount}, balance {balance}' });
    expect(r.errors).toContain('Body must not start with a variable. Add some words before it');
    expect(r.errors).toContain('Body must not end with a variable. Add some words or punctuation after it');
});

test('length limits and no variables outside the body', () => {
    const r = validateItem({ ...base, header: 'x'.repeat(61), footer: 'Hi {amount}', body: base.body + 'y'.repeat(1024) });
    expect(r.errors).toContain('Header is longer than 60 characters');
    expect(r.errors).toContain('Footer cannot contain a variable');
    expect(r.errors).toContain('Body is longer than 1024 characters');
});

test('button text is required and limited', () => {
    const item = { ...base, variables: [], body: 'Pay your bill online now.', buttons: [{ type: 'URL', text: '' }] };
    expect(validateItem(item).errors).toContain('Button 1 text is required');
    expect(validateItem({ ...item, buttons: [{ type: 'URL', text: 'Pay now' }] }).errors).toEqual([]);
});

test('use existing skips text checks', () => {
    expect(validateItem({ ...base, body: '', use_existing: true, existing: { status: 'APPROVED' } }).errors).toEqual([]);
});

test('same name as existing without use-existing is blocked', () => {
    const r = validateItem({ ...base, existing: { status: 'REJECTED' }, existingName: 'payment_confirmation_v2' });
    expect(r.errors[0]).toMatch(/already exists/);
});

test('meta numeric placeholders are rejected', () => {
    expect(validateItem({ ...base, body: base.body + ' Ref {{1}}.' }).errors)
        .toContain('Use "Insert variable" instead of {{1}}-style numbers');
    expect(countPlaceholders('a {amount} b {amount}')).toEqual({ amount: 2 });
});

test('insertAt puts the placeholder at the caret', () => {
    expect(insertAt('Hello world', 6, 6, 'customer_name')).toEqual({ text: 'Hello {customer_name}world', caret: 21 });
    expect(insertAt('Hi', undefined, undefined, 'amount').text).toBe('Hi{amount}');
});
