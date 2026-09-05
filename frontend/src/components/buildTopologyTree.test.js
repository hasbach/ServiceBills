import { buildTopologyTree, PON_UNASSIGNED, nodeMatches } from './buildTopologyTree';

const onu = (over = {}) => ({
    pon_port: 'PON1', onu_id: 'EPON0/1:2', status: 'online',
    mac_address: 'b4:64:15:3f:c1:94', description: 'MoussaGhadir',
    distance_m: 531, customers: [], ...over,
});

const olt = (over = {}) => ({
    id: 2, name: 'V-SOL OLT', host: '192.168.8.100', api_port: 161,
    device_type: 'vsol_olt', last_status: 'online', interface_labels: {},
    last_result_operation: 'olt_status', last_result_at: '2026-09-05 12:00:00',
    last_result: [onu()], children: [], ...over,
});

const ccr = (over = {}) => ({
    id: 1, name: 'CCR1009', host: '192.168.100.1', api_port: 8728,
    device_type: 'mikrotik_ccr', last_status: 'online', interface_labels: {},
    last_result_operation: null, last_result_at: null, last_result: null,
    children: [], ...over,
});

const find = (nodes, kind, label) => {
    for (const n of nodes) {
        if (n.kind === kind && (label === undefined || n.label === label)) return n;
        const hit = find(n.children || [], kind, label);
        if (hit) return hit;
    }
    return null;
};

test('an empty device list yields an empty tree', () => {
    expect(buildTopologyTree([])).toEqual([]);
    expect(buildTopologyTree(undefined)).toEqual([]);
});

test('ONUs group into PON nodes under their OLT', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), onu({ pon_port: 'PON3', mac_address: 'aa:bb:cc:dd:ee:ff' })],
    })] })]);
    const pons = find(tree, 'device', 'V-SOL OLT').children;
    expect(pons.map((p) => p.label)).toEqual(['PON1', 'PON3']);
    expect(pons[0].children).toHaveLength(1);
    expect(pons[0].children[0].kind).toBe('onu');
});

test('a PON node counts its ONUs and how many are up', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), onu({ mac_address: 'aa:bb:cc:dd:ee:01', status: 'offline' })],
    })] })]);
    const pon = find(tree, 'pon', 'PON1');
    expect(pon.meta).toBe('2 ONUs · 1 up');
    expect(pon.status).toBe('up');
});

test('a PON whose every ONU is offline reads as down', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu({ status: 'offline' })],
    })] })]);
    expect(find(tree, 'pon', 'PON1').status).toBe('down');
});

test('an ONU with a missing or malformed pon_port lands under Unassigned, never dropped', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu({ pon_port: undefined }), onu({ pon_port: 42, mac_address: 'aa:bb:cc:dd:ee:02' })],
    })] })]);
    const unassigned = find(tree, 'pon', PON_UNASSIGNED);
    expect(unassigned.children).toHaveLength(2);
});

test('a non-object entry in the result is skipped without throwing', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), null, 'nonsense'],
    })] })]);
    expect(find(tree, 'pon', 'PON1').children).toHaveLength(1);
});

test('a result that is not a list yields no PON nodes and does not throw', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({ last_result: { oops: true } })] })]);
    expect(find(tree, 'device', 'V-SOL OLT').children).toEqual([]);
});

test('customers hang off their ONU with the MAC they are linked by', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu({ customers: [
            { id: 7, name: 'Moussa Ghadir', is_subscription_active: true,
              onu_mac_address: 'b4:64:15:3f:c1:94' },
            { id: 8, name: 'Shop', is_subscription_active: false,
              onu_mac_address: 'b4:64:15:3f:c1:94' },
        ] })],
    })] })]);
    const customers = find(tree, 'onu').children;
    expect(customers.map((c) => c.kind)).toEqual(['customer', 'customer']);
    expect(customers[0].sublabel).toBe('b4:64:15:3f:c1:94');
    expect(customers[1].status).toBe('warn');
});

test('a CCR with a health result gains a Ports branch before its child devices', () => {
    const tree = buildTopologyTree([ccr({
        interface_labels: { ether1: 'MYISP' },
        last_result_operation: 'device_health',
        last_result: { interfaces: [
            { name: 'ether1', running: true, disabled: false, label: 'MYISP' },
            { name: 'ether6', running: false, disabled: false, label: null },
        ] },
        children: [olt()],
    })]);
    const kinds = tree[0].children.map((c) => c.kind);
    expect(kinds).toEqual(['ports', 'device']);
    const ports = find(tree, 'ports');
    expect(ports.meta).toBe('1 of 2 up');
    expect(ports.children[0].label).toBe('MYISP');
    expect(ports.children[0].sublabel).toBe('ether1');
    expect(ports.children[1].label).toBe('ether6');
    expect(ports.children[1].status).toBe('down');
});

test('a disabled interface reads as down, not up', () => {
    const tree = buildTopologyTree([ccr({
        last_result_operation: 'device_health',
        last_result: { interfaces: [{ name: 'ether2', running: true, disabled: true }] },
    })]);
    expect(find(tree, 'interface').status).toBe('down');
});

test('a CCR with no health result has no Ports branch', () => {
    const tree = buildTopologyTree([ccr({ children: [olt()] })]);
    expect(tree[0].children.map((c) => c.kind)).toEqual(['device']);
});

test('every node key is unique across the whole tree', () => {
    const tree = buildTopologyTree([ccr({
        last_result_operation: 'device_health',
        last_result: { interfaces: [{ name: 'ether1', running: true, disabled: false }] },
        children: [olt({ last_result: [onu(), onu({ mac_address: 'aa:bb:cc:dd:ee:03' })] })],
    })]);
    const keys = [];
    (function walk(nodes) {
        nodes.forEach((n) => { keys.push(n.key); walk(n.children || []); });
    })(tree);
    expect(new Set(keys).size).toBe(keys.length);
});

const allKeys = (tree) => {
    const keys = [];
    (function walk(nodes) {
        nodes.forEach((n) => { keys.push(n.key); walk(n.children || []); });
    })(tree);
    return keys;
};

test('two ONUs sharing a MAC in the same PON group still get unique keys', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        // Same pon_port AND same mac_address -- the documented duplicate
        // authorization entry case. Without the index disambiguating,
        // both would collide on `dev-2/pon-PON1/onu-b4:64:15:3f:c1:94`.
        last_result: [onu(), onu()],
    })] })]);
    const keys = allKeys(tree);
    expect(new Set(keys).size).toBe(keys.length);
    const pon = find(tree, 'pon', 'PON1');
    expect(pon.children).toHaveLength(2);
    expect(pon.children[0].key).not.toBe(pon.children[1].key);
});

test('two device_health interfaces sharing a name still get unique keys', () => {
    const tree = buildTopologyTree([ccr({
        last_result_operation: 'device_health',
        last_result: { interfaces: [
            { name: 'ether1', running: true, disabled: false },
            { name: 'ether1', running: false, disabled: false },
        ] },
    })]);
    const keys = allKeys(tree);
    expect(new Set(keys).size).toBe(keys.length);
    const ports = find(tree, 'ports');
    expect(ports.children).toHaveLength(2);
    expect(ports.children[0].key).not.toBe(ports.children[1].key);
});

test('an array entry in the ONU result is skipped, not rendered as a phantom ONU', () => {
    const tree = buildTopologyTree([ccr({ children: [olt({
        last_result: [onu(), [1, 2, 3]],
    })] })]);
    expect(find(tree, 'pon', 'PON1').children).toHaveLength(1);
    expect(find(tree, 'onu', 'ONU')).toBeNull();
});

test('an array entry in the interfaces list is skipped, not rendered as a phantom interface', () => {
    const tree = buildTopologyTree([ccr({
        last_result_operation: 'device_health',
        last_result: { interfaces: [
            { name: 'ether1', running: true, disabled: false },
            [1, 2, 3],
        ] },
    })]);
    const ports = find(tree, 'ports');
    expect(ports.children).toHaveLength(1);
    expect(ports.meta).toBe('1 of 1 up');
});

test('nodeMatches searches label, sublabel and meta case-insensitively', () => {
    const node = { label: 'MoussaGhadir', sublabel: 'b4:64:15:3f:c1:94', meta: '531 m' };
    expect(nodeMatches(node, 'moussa')).toBe(true);
    expect(nodeMatches(node, 'C1:94')).toBe(true);
    expect(nodeMatches(node, '')).toBe(true);
    expect(nodeMatches(node, 'nothing')).toBe(false);
});
