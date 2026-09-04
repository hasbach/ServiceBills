import React, { useState, useEffect, useCallback } from 'react';
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

// green = online/reachable, red = offline/unreachable -- the same convention
// the Network Devices page already uses for last_status chips.
const statusColor = (status) => {
    if (status === 'online') return 'success';
    if (!status) return 'default';
    return 'error';
};

const NetworkTreeView = () => {
    // apiService is a direct module export here, not part of the hook's value
    // -- same as NetworkDeviceManagementView.
    const { setSnackbar } = useAppContext();
    const [tree, setTree] = useState([]);
    const [loading, setLoading] = useState(true);
    const [onusByDevice, setOnusByDevice] = useState({});
    const [errorByDevice, setErrorByDevice] = useState({});
    const [refreshingId, setRefreshingId] = useState(null);
    const [expanded, setExpanded] = useState({});
    const [matcherDevice, setMatcherDevice] = useState(null);

    const loadTree = useCallback(async () => {
        setLoading(true);
        try {
            const res = await apiService.fetchNetworkTree();
            setTree(res.data.tree || []);
        } catch (e) {
            setSnackbar({ open: true, message: 'Failed to load the network tree', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [setSnackbar]);

    useEffect(() => { loadTree(); }, [loadTree]);

    const refreshOlt = async (device) => {
        setRefreshingId(device.id);
        setErrorByDevice((prev) => ({ ...prev, [device.id]: null }));
        try {
            const res = await apiService.refreshOltOnus(device.id);
            if (res.data.ok) {
                setOnusByDevice((prev) => ({ ...prev, [device.id]: res.data.onus }));
                setExpanded((prev) => ({ ...prev, [device.id]: true }));
            } else {
                setErrorByDevice((prev) => ({ ...prev, [device.id]: res.data.message }));
            }
            loadTree();  // pick up the new last_status/last_checked_at
        } catch (e) {
            setErrorByDevice((prev) => ({ ...prev, [device.id]: 'Request failed' }));
        } finally {
            setRefreshingId(null);
        }
    };

    const renderOnu = (onu) => (
        <Box key={onu.mac_address} sx={{ pl: 4, py: 0.75, borderLeft: '2px solid', borderColor: 'divider' }}>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <Chip size="small" label={onu.status} color={statusColor(onu.status)} />
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
                        <Chip size="small" label={device.last_status || 'never checked'}
                            color={statusColor(device.last_status)} />
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
                                    disabled={refreshingId === device.id}
                                    onClick={() => refreshOlt(device)}>
                                    {refreshingId === device.id ? 'Checking…' : 'Load ONUs'}
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
                <Button startIcon={<RefreshIcon />} onClick={loadTree}>Reload</Button>
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
                    onApplied={() => { setMatcherDevice(null); loadTree(); }}
                />
            )}
        </Box>
    );
};

export default NetworkTreeView;
