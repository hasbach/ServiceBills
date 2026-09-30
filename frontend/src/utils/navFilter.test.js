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
