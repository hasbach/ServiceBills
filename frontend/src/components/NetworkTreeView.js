import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
    Box, Typography, Button, Chip, CircularProgress, Alert,
    Stack, Tooltip, TextField,
} from '@mui/material';
import {
    Refresh as RefreshIcon, Link as LinkIcon,
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';
import OnuLabelMatcherDialog from './OnuLabelMatcherDialog';
import pollNetworkJob from './pollNetworkJob';
import TreeNode from './TreeNode';
import { buildTopologyTree } from './buildTopologyTree';
import { filterTopologyTree } from './filterTopologyTree';

// Auto-refresh a device only when its cached result is older than this. Without
// a cap, every visit to the page would fire a fresh 13-second SNMP walk of the
// OLT; with one, opening the page repeatedly is free.
const STALE_AFTER_MS = 5 * 60 * 1000;

/** '2026-09-05 12:00:00' (UTC, as the API emits it) -> "4 min ago". */
export function describeAge(stamp, now = Date.now()) {
    if (!stamp) return 'never checked';
    const then = Date.parse(stamp.replace(' ', 'T') + 'Z');
    if (Number.isNaN(then)) return '';
    const mins = Math.floor((now - then) / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} h ago`;
    return `${Math.floor(hours / 24)} d ago`;
}

function isStale(stamp, now = Date.now()) {
    if (!stamp) return true;
    const then = Date.parse(stamp.replace(' ', 'T') + 'Z');
    return Number.isNaN(then) || (now - then) > STALE_AFTER_MS;
}

/**
 * The set TreeNode actually renders open nodes from: the union of the
 * user's own persisted `expanded` set and whatever the active search is
 * forcing open (`searchExpanded`), minus any per-query override recorded in
 * `searchCollapsed` (see toggleExpansion below). Pure and exported so the
 * two review requirements -- a click during search is never a silent no-op,
 * and clearing the query leaves exactly the expansion state the user asked
 * for -- can be unit-tested directly, without mounting the component (this
 * project has no @testing-library/react).
 */
export function computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed) {
    const next = new Set([...expanded, ...searchExpanded]);
    searchCollapsed.forEach((key) => next.delete(key));
    return next;
}

/**
 * Decide the effect of clicking node `key`, given the three sets above.
 *
 * A node the active search is holding open (its key is in searchExpanded)
 * toggles `searchCollapsed` only -- an override scoped to the current query
 * -- and leaves `expanded` untouched. That is what makes the click always
 * visible immediately (computeEffectiveExpanded subtracts the override from
 * the union, so the node visibly closes/reopens on screen) without ever
 * silently rewriting the user's own persisted choice: `expanded` still says
 * whatever it said before the click, so once the query changes and the
 * override set is thrown away (see the effect in NetworkTreeView that
 * resets searchCollapsed on every `query` change), the node reverts to
 * exactly what `expanded` says on its own -- open if the user had
 * separately, manually opened it; closed if they hadn't.
 *
 * Every other node (not currently forced open by search) toggles `expanded`
 * directly, exactly as it did before search existed.
 *
 * Returns only the one set that actually changed, as { expanded: ... } or
 * { searchCollapsed: ... } (never both), so the caller knows which state
 * setter to call.
 */
export function toggleExpansion(key, { expanded, searchExpanded, searchCollapsed }) {
    if (searchExpanded.has(key)) {
        const next = new Set(searchCollapsed);
        if (next.has(key)) next.delete(key); else next.add(key);
        return { searchCollapsed: next };
    }
    const next = new Set(expanded);
    if (next.has(key)) next.delete(key); else next.add(key);
    return { expanded: next };
}

const NetworkTreeView = () => {
    // apiService is a direct module export here, not part of the hook's value
    // -- same as NetworkDeviceManagementView.
    const { setSnackbar, user } = useAppContext();
    // 'employee'/'collector' can read this page (see NAV_ITEMS in App.js) but
    // must not reach the label matcher, which rewrites Customer.onu_mac_address.
    // Same comma-separated role string App.js parses; the endpoints behind the
    // matcher enforce this too, so this only avoids offering a button that
    // would 403.
    const canEditLinks = (user?.role || '').split(',')
        .map((r) => r.trim().toLowerCase())
        .some((r) => r === 'admin' || r === 'finance');
    const [tree, setTree] = useState([]);
    const [loading, setLoading] = useState(true);
    const [errorByDevice, setErrorByDevice] = useState({});
    // Per-device in-flight indicator -- a plain object keyed by device.id
    // (same keying convention as errorByDevice), NOT a single scalar. A
    // scalar would make one device's "Checking..." state get clobbered by a
    // refresh started on a different device.
    const [refreshingIds, setRefreshingIds] = useState({});
    // Set of expanded node *keys* (buildTopologyTree's node.key, e.g.
    // "dev-12" or "dev-12/pon-1/onu-aa:bb:...") -- not device ids, since a
    // node here can be a PON, an ONU, or a customer as well as a device.
    // This is the user's OWN persisted choice. It is mutated only by
    // toggleNode, declared further down (near the search box state) because
    // it needs to consult searchExpanded/searchCollapsed to decide whether a
    // click should touch this set at all -- see the comment there for why.
    const [expanded, setExpanded] = useState(() => new Set());
    const [matcherDevice, setMatcherDevice] = useState(null);

    // Tenant-wide access mode ('direct' | 'agent') and, in agent mode, the
    // tenant's single on-prem agent (or null if none has been created yet).
    // Fetched independently of the device tree itself -- see the effects
    // below -- since neither lives on a NetworkDevice row.
    const [accessMode, setAccessMode] = useState('direct');
    const [agent, setAgent] = useState(null);

    // Per-device request sequence numbers, used to discard a stale
    // refreshOlt response that resolves after a newer one for the same
    // device (out-of-order network responses). Not component state --
    // updating it must never itself trigger a render.
    const refreshSeqRef = useRef({});

    // Sequence counter for loadTree itself, independent of refreshSeqRef
    // above (which is scoped per-device to refreshOlt). Task 7 multiplied
    // loadTree's call sites -- it now also runs quietly after every
    // completed OLT check, not just on mount and the manual Reload button --
    // so two loadTree calls can be in flight at once with no ordering
    // guarantee on which resolves first. If the older one's response
    // arrives last, applying it would revert the very status chip the
    // quiet resync exists to keep fresh. Same bump-then-compare idiom as
    // refreshSeqRef: increment on entry, capture the value, and only touch
    // state if this call is still the latest -- kept as a ref, not state,
    // so bumping it never itself triggers a render.
    const treeSeqRef = useRef(0);

    // showSpinner distinguishes the genuine first load (full-page spinner is
    // fine, there's nothing on screen yet) from a background resync after an
    // OLT refresh or a label-match apply, which must update state quietly
    // without blanking anything already on screen.
    const loadTree = useCallback(async (showSpinner = true) => {
        const seq = ++treeSeqRef.current;
        if (showSpinner) setLoading(true);
        try {
            const res = await apiService.fetchNetworkTree();
            if (treeSeqRef.current === seq) setTree(res.data.tree || []);
        } catch (e) {
            if (treeSeqRef.current === seq) {
                setSnackbar({ open: true, message: 'Failed to load the network tree', severity: 'error' });
            }
        } finally {
            // Two different questions, gated two different ways: "is my data
            // still the freshest?" (treeSeqRef check) governs setTree/the
            // error snackbar above -- a superseded call must not overwrite a
            // newer response. "Do I own the spinner I turned on?" governs
            // setLoading(false) here, and does NOT take the seq check --
            // whichever call set loading=true is unconditionally responsible
            // for clearing it, superseded or not. Gating this on the seq
            // check too was a real bug: Load ONUs' quiet loadTree(false)
            // resync can bump treeSeqRef after Reload's loadTree(true) set
            // it, so when Reload's own response lands it would see itself as
            // "superseded" and skip setLoading(false) -- leaving the
            // full-page spinner (and no Reload button) on screen forever.
            if (showSpinner) setLoading(false);
        }
    }, [setSnackbar]);

    useEffect(() => { loadTree(); }, [loadTree]);

    // Splices a freshly-finished job's result straight into the in-memory
    // `tree` for one device, keyed by device.id through the same nested
    // `children` walk deviceById below uses to find a device. This is what
    // restores Load ONUs' immediate feedback: buildTopologyTree reads
    // device.last_result to build the PON/ONU nodes, and without this the
    // OLT would sit expanded showing stale (or, on the very first check,
    // empty) PON content until the quiet loadTree(false) resync below lands
    // a second round trip later. That resync still runs afterwards and
    // remains the source of truth (it also refreshes last_status/
    // last_checked_at, which this does not touch) -- this only closes the
    // gap before it arrives.
    const mergeDeviceResult = useCallback((deviceId, result) => {
        setTree((prev) => {
            const update = (device) => {
                if (device.id === deviceId) {
                    return { ...device, last_result: result, last_result_operation: 'olt_status' };
                }
                if (!device.children || !device.children.length) return device;
                return { ...device, children: device.children.map(update) };
            };
            return prev.map(update);
        });
    }, []);

    useEffect(() => {
        apiService.fetchBusinessSettings()
            .then((res) => setAccessMode(res.data?.settings?.network_access_mode || 'direct'))
            .catch(() => {}); // Defaults to 'direct' (today's behaviour) if this fails.
    }, []);

    useEffect(() => {
        if (accessMode !== 'agent') return;
        apiService.fetchNetworkAgents()
            .then((res) => setAgent((res.data || [])[0] || null))
            .catch(() => {}); // Advisory only -- a failed fetch just leaves the chip/buttons as if no agent exists.
    }, [accessMode]);

    const agentOnline = !!(agent && agent.is_online);
    const agentOffline = accessMode === 'agent' && !agentOnline;
    const agentOfflineReason = agent?.last_seen_at
        ? `Agent offline (last seen ${agent.last_seen_at}). Start the agent on your network and try again.`
        : 'Agent offline (never connected). Start the agent on your network and try again.';

    // `auto` distinguishes a manual refresh (the "Load ONUs" button, or
    // applying Match Labels) from the background auto-refresh effect below.
    // Only a manual refresh may expand the node -- see the setExpanded guard
    // further down.
    const refreshOlt = useCallback(async (device, { auto = false } = {}) => {
        const seq = (refreshSeqRef.current[device.id] || 0) + 1;
        refreshSeqRef.current[device.id] = seq;

        setRefreshingIds((prev) => ({ ...prev, [device.id]: true }));
        setErrorByDevice((prev) => ({ ...prev, [device.id]: null }));
        try {
            const res = await apiService.refreshOltOnus(device.id);
            if (!res.data.ok) {
                if (refreshSeqRef.current[device.id] === seq) {
                    setErrorByDevice((prev) => ({ ...prev, [device.id]: res.data.message }));
                }
            } else {
                const job = await pollNetworkJob(res.data.job_id);
                if (refreshSeqRef.current[device.id] === seq) {
                    if (job.status === 'done' && !job.error) {
                        // Merge the job's ONU/customer payload into `tree`
                        // immediately -- buildTopologyTree turns that
                        // straight into PON/ONU nodes -- rather than waiting
                        // for loadTree(false) below to re-fetch the same
                        // data a second round trip later. That resync still
                        // runs (see loadTree(false) call below) and stays
                        // the source of truth for last_status/
                        // last_checked_at, but the ONU list itself must not
                        // wait on it.
                        mergeDeviceResult(device.id, job.result);
                        // Open the OLT's own node so the freshly-loaded PONs
                        // are visible without an extra click -- but only for a
                        // manual refresh. An admin who collapsed a large OLT
                        // to reduce clutter must not have it spontaneously pop
                        // back open because the background auto-refresh
                        // effect (auto=true) happened to fire on it; that
                        // effect fires unasked, exactly like checkDevice
                        // already deliberately does not auto-expand below.
                        if (!auto) {
                            setExpanded((prev) => {
                                const next = new Set(prev);
                                next.add(`dev-${device.id}`);
                                return next;
                            });
                        }
                    } else {
                        setErrorByDevice((prev) => ({ ...prev, [device.id]: job.error }));
                    }
                    // In direct mode (every non-agent tenant today),
                    // _create_device_job just ran the connector inline and
                    // it already stamped last_status/last_checked_at on the
                    // NetworkDevice row -- on every path, success or
                    // failure (see vsol_olt.get_olt_status's _mark_checked
                    // calls). The OLT's status chip and "checked ..."
                    // caption above are rendered from `tree`, not from
                    // local state, so without this quiet resync they'd stay
                    // stale until an unrelated "Reload" or a remount.
                    // Guarded by the same sequence check as the rest of this
                    // function so a superseded poll can't clobber a newer
                    // request's view with a stale reload; showSpinner=false
                    // keeps it from blanking the tree already on screen.
                    loadTree(false);
                }
            }
        } catch (e) {
            if (refreshSeqRef.current[device.id] !== seq) return;
            setErrorByDevice((prev) => ({ ...prev, [device.id]: 'Request failed' }));
        } finally {
            // Only clear the in-flight indicator if no newer request for this
            // device has superseded this one -- otherwise we'd clear the
            // spinner for the request that's actually still running.
            if (refreshSeqRef.current[device.id] === seq) {
                setRefreshingIds((prev) => {
                    const next = { ...prev };
                    delete next[device.id];
                    return next;
                });
            }
        }
    }, [loadTree, mergeDeviceResult]);

    // Same shape as refreshOlt above (per-device refreshSeqRef guard,
    // refreshingIds in-flight map, errorByDevice handling, quiet
    // loadTree(false) resync) but for any non-OLT device -- the auto-refresh
    // effect below is its only caller today. Deliberately does NOT splice
    // its result into `tree` the way refreshOlt's mergeDeviceResult does:
    // mergeDeviceResult hardcodes last_result_operation to 'olt_status',
    // which would wrongly hide a CCR's Ports branch (buildTopologyTree's
    // portsNode gates on last_result_operation === 'device_health') until
    // the resync below lands. Nor does it auto-expand anything -- unlike
    // "Load ONUs", this fires without the user asking for it, so popping a
    // node open on its own would be a surprise. The quiet resync is the
    // sole source of truth here.
    const checkDevice = useCallback(async (device) => {
        const seq = (refreshSeqRef.current[device.id] || 0) + 1;
        refreshSeqRef.current[device.id] = seq;

        setRefreshingIds((prev) => ({ ...prev, [device.id]: true }));
        setErrorByDevice((prev) => ({ ...prev, [device.id]: null }));
        try {
            const res = await apiService.checkNetworkDeviceNow(device.id);
            if (!res.data.ok) {
                if (refreshSeqRef.current[device.id] === seq) {
                    setErrorByDevice((prev) => ({ ...prev, [device.id]: res.data.message }));
                }
            } else {
                const job = await pollNetworkJob(res.data.job_id);
                if (refreshSeqRef.current[device.id] === seq) {
                    if (job.status === 'done' && !job.error) {
                        loadTree(false);
                    } else {
                        setErrorByDevice((prev) => ({ ...prev, [device.id]: job.error }));
                    }
                }
            }
        } catch (e) {
            if (refreshSeqRef.current[device.id] !== seq) return;
            setErrorByDevice((prev) => ({ ...prev, [device.id]: 'Request failed' }));
        } finally {
            if (refreshSeqRef.current[device.id] === seq) {
                setRefreshingIds((prev) => {
                    const next = { ...prev };
                    delete next[device.id];
                    return next;
                });
            }
        }
    }, [loadTree]);

    // The tree already renders from each device's cached result, so this is a
    // background top-up, not a load. It deliberately runs at most once per
    // device per mount, and never when the agent is offline (in agent mode the
    // job would just be refused).
    const autoRefreshedRef = useRef(new Set());
    useEffect(() => {
        if (accessMode === 'agent' && !agentOnline) return;
        // Collect this run's stale devices and mark them in autoRefreshedRef
        // *synchronously*, before anything is awaited below. loadTree(false)
        // resyncs inside refreshOlt/checkDevice mutate `tree`, which re-runs
        // this effect while the loop below is still going -- marking eagerly
        // is what keeps that re-entrant run from re-collecting (and
        // re-firing) the same devices, preserving the once-per-mount
        // guarantee across effect re-runs, not just within one run.
        const stale = [];
        tree.forEach((root) => {
            const walk = (device) => {
                if (!autoRefreshedRef.current.has(device.id)
                        && isStale(device.last_result_at)) {
                    autoRefreshedRef.current.add(device.id);
                    stale.push(device);
                }
                (device.children || []).forEach(walk);
            };
            walk(root);
        });
        if (!stale.length) return;
        // Refresh one device at a time, awaiting each before starting the
        // next, rather than firing every stale device at once. In 'direct'
        // mode (today's default) the backend runs the connector *inline*,
        // blocking the request thread for the whole walk (~13s for an OLT),
        // and production runs a single synchronous gunicorn worker shared by
        // every tenant. The first time any tenant opens this page after a
        // deploy, last_result_at has never been written, so every device on
        // the tree is stale at once -- firing them all together would stall
        // that one worker for the sum of every check's duration. Doing them
        // one at a time tops the page up gradually instead. Each call is
        // individually caught so a failure on one device (agent drops
        // mid-flight, bad credentials, a connectivity blip) can't stop the
        // rest -- refreshOlt/checkDevice already record their own failure in
        // errorByDevice; this catch exists only to keep the loop going in
        // case one somehow rejects instead.
        (async () => {
            for (const device of stale) {
                try {
                    if (device.device_type === 'vsol_olt') await refreshOlt(device, { auto: true });
                    else await checkDevice(device);
                } catch (e) {
                    // Swallowed on purpose -- see comment above.
                }
            }
        })();
        // refreshOlt/checkDevice are stable for a given device set.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [tree, accessMode, agentOnline]);

    // The node array from buildTopologyTree carries deviceId but not the raw
    // device row (device_type, host, ...) the action buttons below need --
    // that only lives on the API tree's own device objects, which nest
    // children rather than being a flat list. Walked once per tree fetch.
    const deviceById = useMemo(() => {
        const map = new Map();
        const walk = (device) => {
            map.set(device.id, device);
            (device.children || []).forEach(walk);
        };
        tree.forEach(walk);
        return map;
    }, [tree]);

    const topology = useMemo(() => {
        const now = Date.now();
        const decorate = (node) => ({
            ...node,
            ageLabel: node.kind === 'device' ? describeAge(node.lastResultAt, now) : undefined,
            children: (node.children || []).map(decorate),
        });
        return buildTopologyTree(tree).map(decorate);
    }, [tree]);

    // Search box state -- filters `topology` down to matching branches and
    // reports which ancestor keys must be forced open for every hit to be
    // visible. Kept separate from `expanded` (the user's own manual
    // expand/collapse state) rather than folded into it, so typing a search
    // never discards branches the user opened by hand; the two sets are
    // unioned below into what TreeNode actually renders from.
    const [query, setQuery] = useState('');
    const { nodes: visibleTree, expandedKeys: searchExpanded } = useMemo(
        () => filterTopologyTree(topology, query), [topology, query]);

    // Per-query override of which search-forced nodes the user has clicked
    // closed -- see toggleExpansion's doc comment above for the full
    // rationale (this is the fix for a reviewed bug: expand/collapse used to
    // silently corrupt `expanded`, the user's own persisted state, whenever
    // it was clicked while a search held the same node open). Reset whenever
    // `query` changes so an override never leaks from one search into the
    // next, or lingers once the box is cleared.
    const [searchCollapsed, setSearchCollapsed] = useState(() => new Set());
    useEffect(() => {
        // Guard against replacing an already-empty Set with a new empty Set
        // every time `query` changes character-by-character while nothing
        // has actually been overridden yet -- avoids a pointless extra
        // render on every keystroke.
        setSearchCollapsed((prev) => (prev.size ? new Set() : prev));
    }, [query]);

    // Declared here rather than beside `expanded`'s own state because it
    // needs searchExpanded, which derives from `topology` (itself derived
    // from `tree`) and so cannot exist any earlier in this component.
    const toggleNode = useCallback((key) => {
        const result = toggleExpansion(key, { expanded, searchExpanded, searchCollapsed });
        if (result.searchCollapsed) setSearchCollapsed(result.searchCollapsed);
        else setExpanded(result.expanded);
    }, [expanded, searchExpanded, searchCollapsed]);

    const effectiveExpanded = useMemo(
        () => computeEffectiveExpanded(expanded, searchExpanded, searchCollapsed),
        [expanded, searchExpanded, searchCollapsed]);

    // Renders *Match Labels* and *Load ONUs* (OLT-only) into a device node's
    // card -- moved here unchanged from the old renderDevice, including the
    // agentOffline-driven disabling/tooltips and the canEditLinks gate on
    // Match Labels. errorByDevice/refreshingIds are NOT OLT-only, though:
    // checkDevice (the auto-refresh path for every non-OLT device, e.g. a
    // CCR's device_health check) writes into both of those same maps, so a
    // failed or in-flight check on a CCR needs to be visible here too, or it
    // is written to state nothing ever renders. This is the only per-device
    // injection point TreeNode exposes.
    const deviceActions = useCallback((node) => {
        const device = deviceById.get(node.deviceId);
        if (!device) return null;
        const isOlt = device.device_type === 'vsol_olt';
        const isRefreshing = !!refreshingIds[device.id];
        const error = errorByDevice[device.id];
        // A non-OLT device with nothing to show (no error, nothing in
        // flight) renders no actions at all, same as before this fix --
        // returning an empty fragment instead of null here would make
        // TreeNode render an empty .nt-actions div (which carries its own
        // margin-top) under every CCR card even when idle.
        if (!isOlt && !isRefreshing && !error) return null;
        return (
            <>
                {isOlt && canEditLinks && (
                    <Tooltip title={agentOffline ? agentOfflineReason : ''}>
                        <span>
                            <Button size="small" startIcon={<LinkIcon />}
                                disabled={agentOffline}
                                onClick={() => setMatcherDevice(device)}>
                                Match Labels
                            </Button>
                        </span>
                    </Tooltip>
                )}
                {isOlt && (
                    <Tooltip title={agentOffline ? agentOfflineReason : ''}>
                        <span>
                            <Button size="small" variant="outlined" startIcon={<RefreshIcon />}
                                disabled={isRefreshing || agentOffline}
                                onClick={() => refreshOlt(device)}>
                                {isRefreshing ? 'Checking…' : 'Load ONUs'}
                            </Button>
                        </span>
                    </Tooltip>
                )}
                {/* Non-OLT devices have no manual refresh button to carry a
                    "Checking…" label, but a background checkDevice call is
                    still in flight and still needs a visible indicator. */}
                {!isOlt && isRefreshing && (
                    <Typography variant="caption" color="text.secondary">Checking…</Typography>
                )}
                {error && (
                    <Alert severity="error" className="nt-action-error">{error}</Alert>
                )}
            </>
        );
    }, [deviceById, agentOffline, agentOfflineReason, refreshingIds, canEditLinks, errorByDevice, refreshOlt]);

    if (loading) return <Box sx={{ p: 4, textAlign: 'center' }}><CircularProgress /></Box>;

    return (
        <Box>
            <Stack direction="row" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h5" sx={{ fontWeight: 600 }}>Network Tree</Typography>
                <Box sx={{ flexGrow: 1 }} />
                <Button startIcon={<RefreshIcon />} onClick={() => loadTree()}>Reload</Button>
            </Stack>

            {/* No agent in 'direct' mode -- rendering this would just be noise. */}
            {accessMode === 'agent' && (
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                    <Chip size="small" color={agentOnline ? 'success' : 'error'}
                        label={agentOnline ? 'Agent online' : 'Agent offline'} />
                    <Typography variant="caption" color="text.secondary">
                        {agent?.last_seen_at ? `last seen ${agent.last_seen_at}` : 'never connected'}
                    </Typography>
                </Stack>
            )}

            {tree.length === 0 ? (
                <Alert severity="info">
                    No network devices yet. Add a CCR and an OLT on the Network Devices page,
                    then set the OLT's "Connected To" to the CCR.
                </Alert>
            ) : (
                <>
                    <TextField
                        size="small"
                        fullWidth
                        placeholder="Search customers, ONUs, MACs…"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        sx={{ mb: 2 }}
                    />
                    {query && visibleTree.length === 0 ? (
                        <Alert severity="info">{`No matches for "${query}"`}</Alert>
                    ) : (
                        <Box className="nt-root" sx={{
                            '--nt-surface': (t) => t.palette.background.paper,
                            '--nt-border': (t) => t.palette.divider,
                            '--nt-border-strong': (t) => t.palette.text.secondary,
                            '--nt-link': (t) => t.palette.divider,
                            '--nt-up': (t) => t.palette.success.main,
                            '--nt-down': (t) => t.palette.error.main,
                            '--nt-down-bg': (t) => t.palette.error.light + '22',
                            '--nt-warn': (t) => t.palette.warning.main,
                            '--nt-muted': (t) => t.palette.text.disabled,
                            '--nt-accent': (t) => t.palette.primary.main,
                        }}>
                            <div className="nt-level">
                                {visibleTree.map((root) => (
                                    <TreeNode key={root.key} node={root} expanded={effectiveExpanded}
                                              onToggle={toggleNode} liveLinks actions={deviceActions} />
                                ))}
                            </div>
                        </Box>
                    )}
                </>
            )}

            {matcherDevice && (
                <OnuLabelMatcherDialog
                    device={matcherDevice}
                    onClose={() => setMatcherDevice(null)}
                    onApplied={() => {
                        // Re-run the ONU refresh for this OLT (through the
                        // same refreshOlt path used by "Load ONUs", so the
                        // per-device refreshingIds/refreshSeqRef guards stay
                        // intact) rather than loadTree(), which only refetches
                        // the device skeleton and carries no ONU/customer
                        // data -- without this, links just applied here stay
                        // invisible until a separate manual refresh.
                        const d = matcherDevice;
                        setMatcherDevice(null);
                        refreshOlt(d);
                    }}
                />
            )}
        </Box>
    );
};

export default NetworkTreeView;
