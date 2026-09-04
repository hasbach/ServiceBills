import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box, Typography, Button, Paper, Chip, CircularProgress, Alert,
    Collapse, IconButton, Stack, Divider,
} from '@mui/material';
import {
    ExpandMore as ExpandMoreIcon, ChevronRight as ChevronRightIcon,
    Refresh as RefreshIcon, Router as CcrIcon, SettingsInputAntenna as OltIcon,
    Person as PersonIcon, Link as LinkIcon,
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';
import OnuLabelMatcherDialog from './OnuLabelMatcherDialog';
import { STATUS_COLOR, STATUS_LABEL } from './deviceStatus';

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

    // Per-device request sequence numbers, used to discard a stale
    // refreshOlt response that resolves after a newer one for the same
    // device (out-of-order network responses). Not component state --
    // updating it must never itself trigger a render.
    const refreshSeqRef = useRef({});

    // showSpinner distinguishes the genuine first load (full-page spinner is
    // fine, there's nothing on screen yet) from a background resync after an
    // OLT refresh or a label-match apply, which must update state quietly
    // without blanking anything already on screen.
    const loadTree = useCallback(async (showSpinner = true) => {
        if (showSpinner) setLoading(true);
        try {
            const res = await apiService.fetchNetworkTree();
            setTree(res.data.tree || []);
        } catch (e) {
            setSnackbar({ open: true, message: 'Failed to load the network tree', severity: 'error' });
        } finally {
            if (showSpinner) setLoading(false);
        }
    }, [setSnackbar]);

    useEffect(() => { loadTree(); }, [loadTree]);

    const refreshOlt = async (device) => {
        const seq = (refreshSeqRef.current[device.id] || 0) + 1;
        refreshSeqRef.current[device.id] = seq;

        setRefreshingIds((prev) => ({ ...prev, [device.id]: true }));
        setErrorByDevice((prev) => ({ ...prev, [device.id]: null }));
        try {
            const res = await apiService.refreshOltOnus(device.id);
            // A newer refresh for this same device has started since this
            // request went out -- this response is stale, discard it silently.
            if (refreshSeqRef.current[device.id] !== seq) return;
            if (res.data.ok) {
                setOnusByDevice((prev) => ({ ...prev, [device.id]: res.data.onus }));
                setExpanded((prev) => ({ ...prev, [device.id]: true }));
            } else {
                setErrorByDevice((prev) => ({ ...prev, [device.id]: res.data.message }));
            }
            loadTree(false);  // background sync to pick up the new last_status/last_checked_at
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
                {onu.customers.length === 0 ? (
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
                            label={device.last_status ? (STATUS_LABEL[device.last_status] || device.last_status) : 'Never checked'}
                            color={device.last_status ? (STATUS_COLOR[device.last_status] || 'default') : 'default'} />
                        {device.last_checked_at && (
                            <Typography variant="caption" color="text.secondary">
                                checked {device.last_checked_at}
                            </Typography>
                        )}
                        <Box sx={{ flexGrow: 1 }} />
                        {isOlt && (
                            <>
                                <Button size="small" startIcon={<LinkIcon />}
                                    onClick={() => setMatcherDevice(device)}>
                                    Match Labels
                                </Button>
                                <Button size="small" variant="outlined" startIcon={<RefreshIcon />}
                                    disabled={isRefreshing}
                                    onClick={() => refreshOlt(device)}>
                                    {isRefreshing ? 'Checking…' : 'Load ONUs'}
                                </Button>
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
