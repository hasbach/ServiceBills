/**
 * Turn the /api/network-tree payload into the node array the page renders.
 *
 * Pure on purpose: every rule about what the tree's levels ARE lives here and
 * nowhere else, so it can be unit-tested without rendering anything, and
 * TreeNode stays a presentational component with no knowledge of PON ports or
 * interfaces.
 *
 * Deliberately defensive throughout. `last_result` is whatever JSON was stored
 * when the job completed -- in agent mode, whatever the on-prem agent posted --
 * so a malformed entry must be skipped or bucketed, never dropped silently in
 * a way that makes real hardware vanish from the page, and never thrown on.
 */

import { parseUtc, formatStamp } from './formatStamp';

export const PON_UNASSIGNED = 'Unassigned';

const asArray = (value) => (Array.isArray(value) ? value : []);

/** ONU/interface status collapses to the four the UI paints. */
const onuStatus = (status) => (status === 'online' ? 'up' : 'down');

const interfaceStatus = (iface) =>
    (iface.running && !iface.disabled ? 'up' : 'down');

function interfaceStatus_isUp(iface) {
    return interfaceStatus(iface) === 'up';
}

const deviceStatus = (lastStatus) => {
    if (lastStatus === 'online') return 'up';
    if (!lastStatus) return 'unknown';
    return 'down';   // 'unreachable', 'auth_failed', anything else
};

const searchTextOf = (...parts) =>
    parts.filter(Boolean).join(' ').toLowerCase();

/** Case-insensitive substring match over a node's visible text. */
export function nodeMatches(node, query) {
    const q = (query || '').trim().toLowerCase();
    if (!q) return true;
    const text = node.searchText
        || searchTextOf(node.label, node.sublabel, node.meta);
    return text.includes(q);
}

/**
 * Compute the group-local part of a child's key that is BOTH unique across
 * the group (a React key requirement) AND stable across rebuilds for the
 * same real-world identity (a MAC address, an interface name, a customer
 * id, ...) -- independent of where the walk happened to place it this time.
 *
 * Keys on `identity` alone when it is present, so re-polling the same
 * hardware in a different order does not change its key. `seenCounts` is a
 * Map shared across one group's iteration: occurrences of the same identity
 * value are counted, and every occurrence after the first has `#n` appended,
 * so real duplicates (a stale duplicate ONU authorization, two interfaces
 * both literally named "ether1") still get distinct keys. With no usable
 * identity there is nothing to be stable on, so this falls back to the
 * group-local index, exactly as before this fix.
 */
function keyPart(identity, index, seenCounts) {
    if (!identity) return `${index}-na`;
    const seen = (seenCounts.get(identity) || 0) + 1;
    seenCounts.set(identity, seen);
    return seen === 1 ? identity : `${identity}#${seen}`;
}

function customerNode(customer, parentKey, index, seenIds, lastLocateAt) {
    // A customer located in the most recent run renders plainly. One whose
    // CPE was not seen still shows under their last known ONU -- that is the
    // memory -- but says so, because during an outage "here" and "here as of
    // Thursday" are very different facts. A customer with no onu_last_seen_at
    // at all has never been located -- a different fact from having been
    // located and since gone quiet -- so they show nothing rather than being
    // mistaken for stale.
    const id = customer.id !== undefined && customer.id !== null ? String(customer.id) : '';
    const seen = parseUtc(customer.onu_last_seen_at);
    const run = parseUtc(lastLocateAt);
    const remembered = !Number.isNaN(seen) && !Number.isNaN(run) && seen < run;
    return {
        key: `${parentKey}/cust-${keyPart(id, index, seenIds)}`,
        kind: 'customer',
        label: customer.name || 'Unnamed customer',
        sublabel: customer.onu_mac_address || '',
        meta: remembered ? `last seen ${formatStamp(customer.onu_last_seen_at)}` : '',
        status: customer.is_subscription_active ? 'up' : 'warn',
        searchText: searchTextOf(customer.name, customer.onu_mac_address),
        children: [],
    };
}

function onuNode(onu, ponKey, index, seenMacs, lastLocateAt) {
    const mac = typeof onu.mac_address === 'string' ? onu.mac_address : '';
    const key = `${ponKey}/onu-${keyPart(mac, index, seenMacs)}`;
    const distance = Number(onu.distance_m) > 0 ? `${onu.distance_m} m` : '';
    const seenCustomerIds = new Map();
    return {
        key,
        kind: 'onu',
        label: onu.description || onu.onu_id || mac || 'ONU',
        sublabel: mac,
        meta: distance,
        status: onuStatus(onu.status),
        searchText: searchTextOf(onu.description, onu.onu_id, mac),
        children: asArray(onu.customers)
            .filter((c) => c && typeof c === 'object' && !Array.isArray(c))
            .map((c, i) => customerNode(c, key, i, seenCustomerIds, lastLocateAt)),
    };
}

/** Group an OLT's ONU list into PON nodes. Order follows first appearance. */
function ponNodes(device, lastLocateAt) {
    const onus = asArray(device.last_result)
        .filter((o) => o && typeof o === 'object' && !Array.isArray(o));
    const groups = new Map();
    onus.forEach((onu) => {
        // A pon_port that is missing, blank, or not a string cannot be trusted
        // as a group name -- but the ONU is still real hardware, so it gets a
        // bucket rather than being dropped.
        const port = typeof onu.pon_port === 'string' && onu.pon_port.trim()
            ? onu.pon_port.trim()
            : PON_UNASSIGNED;
        if (!groups.has(port)) groups.set(port, []);
        groups.get(port).push(onu);
    });

    return [...groups.entries()].map(([port, members]) => {
        const key = `dev-${device.id}/pon-${port}`;
        const up = members.filter((o) => o.status === 'online').length;
        const seenMacs = new Map();
        return {
            key,
            kind: 'pon',
            label: port,
            sublabel: '',
            meta: `${members.length} ONU${members.length === 1 ? '' : 's'} · ${up} up`,
            status: up > 0 ? 'up' : 'down',
            searchText: searchTextOf(port),
            children: members.map((onu, i) => onuNode(onu, key, i, seenMacs, lastLocateAt)),
        };
    });
}

/** The synthetic Ports branch: a CCR's own interfaces, as a sibling of its children. */
function portsNode(device) {
    const result = device.last_result;
    if (device.last_result_operation !== 'device_health'
        || !result || typeof result !== 'object') return null;
    const interfaces = asArray(result.interfaces)
        .filter((i) => i && typeof i === 'object' && !Array.isArray(i));
    if (!interfaces.length) return null;

    const key = `dev-${device.id}/ports`;
    const up = interfaces.filter(interfaceStatus_isUp).length;
    const seenNames = new Map();
    return {
        key,
        kind: 'ports',
        label: 'Ports',
        sublabel: '',
        meta: `${up} of ${interfaces.length} up`,
        status: up > 0 ? 'up' : 'down',
        searchText: 'ports interfaces',
        children: interfaces.map((iface, i) => {
            const name = typeof iface.name === 'string' ? iface.name : '';
            const label = typeof iface.label === 'string' && iface.label ? iface.label : '';
            return {
                key: `${key}/if-${keyPart(name, i, seenNames)}`,
                kind: 'interface',
                label: label || name || 'interface',
                sublabel: label ? name : '',
                meta: '',
                status: interfaceStatus(iface),
                searchText: searchTextOf(label, name),
                children: [],
            };
        }),
    };
}

function deviceNode(device) {
    const children = [];
    const ports = portsNode(device);
    if (ports) children.push(ports);           // Ports always precedes child devices
    if (device.device_type === 'vsol_olt') {
        children.push(...ponNodes(device, device.lastLocateAt || null));
    }
    asArray(device.children).forEach((child) => children.push(deviceNode(child)));

    return {
        key: `dev-${device.id}`,
        kind: 'device',
        label: device.name || 'Device',
        sublabel: `${device.host || ''}${device.api_port ? `:${device.api_port}` : ''}`,
        meta: '',
        status: deviceStatus(device.last_status),
        deviceId: device.id,
        deviceType: device.device_type,
        lastResultAt: device.last_result_at || null,
        searchText: searchTextOf(device.name, device.host),
        children,
    };
}

export function buildTopologyTree(devices) {
    return asArray(devices).map(deviceNode);
}
