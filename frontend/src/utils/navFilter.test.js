import { filterNavItems } from './navFilter';

const items = [
    { key: 'dashboard', allowedRoles: ['admin'] },
    { key: 'network-tree', allowedRoles: ['admin'], module: 'network' },
    { key: 'upstream-providers', allowedRoles: ['admin'], module: 'upstream_sync', visibleWhen: (bs) => bs?.network_mode === 'upstream_bridge' },
    { key: 'employees', allowedRoles: ['finance'] },
];
const hasRole = (r) => r === 'admin';

test('hides items whose module is off', () => {
    const got = filterNavItems(items, { hasRole, businessSettings: { network_mode: 'upstream_bridge' }, hasModule: (m) => m !== 'network' });
    expect(got.map(i => i.key)).toEqual(['dashboard', 'upstream-providers']);
});

test('still applies roles and visibleWhen', () => {
    const got = filterNavItems(items, { hasRole, businessSettings: {}, hasModule: () => true });
    expect(got.map(i => i.key)).toEqual(['dashboard', 'network-tree']);
});

test('saasOnly items are hidden on-prem only', () => {
    const its = [{ key: 'billing', saasOnly: true }, { key: 'x' }];
    const base = { hasRole: () => true, businessSettings: {}, hasModule: () => true };
    expect(filterNavItems(its, { ...base, isOnprem: true }).map(i => i.key)).toEqual(['x']);
    expect(filterNavItems(its, { ...base, isOnprem: false }).map(i => i.key)).toEqual(['billing', 'x']);
    expect(filterNavItems(its, base).map(i => i.key)).toEqual(['billing', 'x']);
});
