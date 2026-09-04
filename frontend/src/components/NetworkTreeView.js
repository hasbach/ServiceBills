import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box, Typography, Button, Paper, Chip, CircularProgress, Alert,
    Collapse, IconButton, Stack, Divider, Tooltip,
} from '@mui/material';
import {
    ExpandMore as ExpandMoreIcon, ChevronRight as ChevronRightIcon,
    Refresh as RefreshIcon, Router as CcrIcon, SettingsInputAntenna as OltIcon,
    Person as PersonIcon, Link as LinkIcon,
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';
import OnuLabelMatcherDialog from './OnuLabelMatcherDialog';
import pollNetworkJob from './pollNetworkJob';
import { STATUS_COLOR, STATUS_LABEL, NOT_CHECKED } from './deviceStatus';

// ONU-level status is only ever 'online'/'offline' (see vsol_olt.py
// get_olt_status) -- a simpler two-state domain than NetworkDevice's
// last_status, so it keeps its own local color helper rather than using the
// shared STATUS_COLOR/STATUS_LABEL maps (those cover 'auth_failed' too,
// which never applies to an individual ONU).
const onuStatusColor = (status) => (status === 'online' ? 'success' : 'error');

const NetworkTreeView = () => {
    // apiService is a direct module export here, not part of the hook's value
    // -- same as NetworkDeviceManagementView.
    const { setSnackbar } = useAppContext();
    const [tree, setTree] = useState([]);
    const [loading, setLoading] = useState(true);
    const [onusByDevice, setOnusByDevice] = useState({});
    const [errorByDevice, setErrorByDevice] = useState({});
    // Per-device in-flight indicator -- a plain object keyed by device.id
    // (same keying convention as onusByDevice/errorByDevice), NOT a single
    // scalar. A scalar would make one device's "Checking..." state get
    // clobbered by a refresh started on a different device.
    const [refreshingIds, setRefreshingIds] = useState({});
    const [expanded, setExpanded] = useState({});
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

    const refreshOlt = async (device) => {
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
                        setOnusByDevice((prev) => ({ ...prev, [device.id]: job.result }));
                        setExpanded((prev) => ({ ...prev, [device.id]: true }));
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
                    // onusByDevice/errorByDevice, so without this quiet
                    // resync they'd stay stale until an unrelated "Reload"
                    // or a remount. Guarded by the same sequence check as
                    // the rest of this function so a superseded poll can't
                    // clobber a newer request's view with a stale reload;
                    // showSpinner=false keeps it from blanking the tree
                    // already on screen.
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
    };

    const renderOnu = (onu) => (
        <Box key={onu.mac_address} sx={{ pl: 4, py: 0.75, borderLeft: '2px solid', borderColor: 'divider' }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <Chip size="small" label={onu.status} color={onuStatusColor(onu.status)} />
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{onu.onu_id}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
                    {onu.mac_address}
                </Typography>
                {onu.description && <Chip size="small" variant="outlined" label={onu.description} />}
                {onu.distance_m > 0 && (
                    <Typography variant="caption" color="text.secondary">{onu.distance_m} m</Typography>
                )}
            </Stack>
            <Box sx={{ pl: 2, pt: 0.5 }}>
                {(onu.customers || []).length === 0 ? (
                    <Typography variant="caption" color="text.secondary">
                        No customer linked to this ONU
                    </Typography>
                ) : onu.customers.map((c) => (
                    <Stack key={c.id} direction="row" spacing={0.5} alignItems="center">
                        <PersonIcon fontSize="inherit" color="action" />
                        <Typography variant="body2">{c.name}</Typography>
                        {!c.is_subscription_active && (
                            <Chip size="small" color="warning" variant="outlined" label="inactive" />
                        )}
                    </Stack>
                ))}
            </Box>
        </Box>
    );

    const renderDevice = (device, depth) => {
        const isOlt = device.device_type === 'vsol_olt';
        const onus = onusByDevice[device.id];
        const isOpen = !!expanded[device.id];
        const isRefreshing = !!refreshingIds[device.id];
        return (
            <Box key={device.id} sx={{ pl: depth * 3 }}>
                <Paper variant="outlined" sx={{ p: 1.5, mb: 1 }}>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                        {isOlt && onus && (
                            <IconButton size="small"
                                onClick={() => setExpanded((p) => ({ ...p, [device.id]: !isOpen }))}>
                                {isOpen ? <ExpandMoreIcon /> : <ChevronRightIcon />}
                            </IconButton>
                        )}
                        {isOlt ? <OltIcon color="action" /> : <CcrIcon color="action" />}
                        <Typography sx={{ fontWeight: 600 }}>{device.name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                            {device.host}:{device.api_port}
                        </Typography>
                        <Chip size="small"
                            label={STATUS_LABEL[device.last_status || NOT_CHECKED] || device.last_status}
                            color={STATUS_COLOR[device.last_status || NOT_CHECKED] || 'default'} />
                        {device.last_checked_at && (
                            <Typography variant="caption" color="text.secondary">
                                checked {device.last_checked_at}
                            </Typography>
                        )}
                        <Box sx={{ flexGrow: 1 }} />
                        {isOlt && (
                            <>
                                <Tooltip title={agentOffline ? agentOfflineReason : ''}>
                                    <span>
                                        <Button size="small" startIcon={<LinkIcon />}
                                            disabled={agentOffline}
                                            onClick={() => setMatcherDevice(device)}>
                                            Match Labels
                                        </Button>
                                    </span>
                                </Tooltip>
                                <Tooltip title={agentOffline ? agentOfflineReason : ''}>
                                    <span>
                                        <Button size="small" variant="outlined" startIcon={<RefreshIcon />}
                                            disabled={isRefreshing || agentOffline}
                                            onClick={() => refreshOlt(device)}>
                                            {isRefreshing ? 'Checking…' : 'Load ONUs'}
                                        </Button>
                                    </span>
                                </Tooltip>
                            </>
                        )}
                    </Stack>

                    {errorByDevice[device.id] && (
                        <Alert severity="error" sx={{ mt: 1 }}>{errorByDevice[device.id]}</Alert>
                    )}

                    {isOlt && onus && (
                        <Collapse in={isOpen}>
                            <Divider sx={{ my: 1 }} />
                            <Typography variant="caption" color="text.secondary">
                                {onus.length} ONUs — {onus.filter((o) => o.status === 'online').length} online,
                                {' '}{onus.filter((o) => o.status !== 'online').length} offline
                            </Typography>
                            <Box sx={{ mt: 1 }}>{onus.map(renderOnu)}</Box>
                        </Collapse>
                    )}
                </Paper>
                {device.children.map((child) => renderDevice(child, depth + 1))}
            </Box>
        );
    };

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
            ) : tree.map((root) => renderDevice(root, 0))}

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
