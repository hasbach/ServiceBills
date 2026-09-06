import React, { useState, useEffect, useRef } from 'react';
import {
    Box, Typography, Button, TextField, Dialog, DialogTitle,
    DialogContent, DialogActions, Grid, Paper, TableContainer,
    Table, TableHead, TableRow, TableCell, TableBody, MenuItem,
    IconButton, Tooltip, Chip, CircularProgress, Switch, FormControlLabel, Stack,
    Alert,
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    NetworkCheck as CheckNowIcon
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';
import pollNetworkJob from './pollNetworkJob';
import { STATUS_COLOR, STATUS_LABEL, NOT_CHECKED } from './deviceStatus';
import { formatStamp } from './formatStamp';

// A network device the tenant owns (starting with a core CCR), monitored for
// RouterOS-level health -- independent of MikrotikServer, which is
// specifically about local PPPoE secret management. See
// docs/superpowers/specs/2026-09-01-network-device-health-monitoring-design.md.
const NetworkDeviceManagementView = () => {
    const { setSnackbar } = useAppContext();
    const [devices, setDevices] = useState([]);
    const [loading, setLoading] = useState(true);
    const [checkingId, setCheckingId] = useState(null);

    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingDevice, setEditingDevice] = useState(null);

    const [healthDialogOpen, setHealthDialogOpen] = useState(false);
    const [healthDevice, setHealthDevice] = useState(null);
    const [health, setHealth] = useState(null);
    // Check-now result for a vsol_olt device: the ONU list (health stays
    // null for this device type -- see handleCheckNow). Kept separate from
    // `health` so the dialog can tell at a glance which device type's
    // result it's holding, rather than inferring it from field shape.
    const [onuResult, setOnuResult] = useState(null);
    const [healthError, setHealthError] = useState(null);
    const [labelDrafts, setLabelDrafts] = useState({});

    // checkingId/health/onuResult/healthError above are scalars shared by
    // whichever single device's "Check Now" dialog is open -- unlike
    // NetworkTreeView's errorByDevice/refreshingIds, which are
    // objects keyed by device id and so can hold several devices' results
    // at once without collision. A poll now runs for up to 180s (see
    // pollNetworkJob), so it's entirely realistic to check device A, close
    // the dialog, check device B, and have A's poll resolve after B's is
    // already on screen. These two refs (mutable, so updating them never
    // itself triggers a render -- same reasoning as NetworkTreeView's
    // refreshSeqRef) together answer "is this resolving check still the
    // one whose result belongs in the shared state right now":
    // - checkSeqRef is a per-device request counter, same idea as
    //   refreshSeqRef, so a second check on the SAME device supersedes a
    //   first one still in flight.
    // - activeDeviceIdRef is the device the shared scalar state currently
    //   belongs to, so a check for a device the user has since navigated
    //   away from is dropped even though it was never superseded by
    //   another check on that same device.
    const checkSeqRef = useRef({});
    const activeDeviceIdRef = useRef(null);

    // Tenant-wide access mode ('direct' | 'agent') and, in agent mode, the
    // tenant's single on-prem agent (or null if none exists yet). Neither
    // lives on a NetworkDevice row, so both are fetched independently below.
    const [accessMode, setAccessMode] = useState('direct');
    const [agent, setAgent] = useState(null);

    useEffect(() => {
        loadDevices();
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
        ? `Agent offline (last seen ${formatStamp(agent.last_seen_at)}). Start the agent on your network and try again.`
        : 'Agent offline (never connected). Start the agent on your network and try again.';

    const loadDevices = async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchNetworkDevices();
            setDevices(response.data);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load network devices', severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    const handleSaveDevice = async () => {
        // In agent mode the password/community field is hidden entirely (the
        // credential lives in agent.toml on the on-prem box, not here), so
        // it's never required there -- only in direct mode, where the cloud
        // still calls the device itself.
        if (accessMode !== 'agent' && !editingDevice.id && !editingDevice.password) {
            setSnackbar({ open: true, message: 'Password is required', severity: 'warning' });
            return;
        }
        // Never send a `password` key while the field is hidden -- the
        // backend rejects a supplied password in agent mode with a 400, and
        // sending an empty string would be worse than sending nothing (it
        // would read as "clear the value the user never saw").
        const { password, ...deviceWithoutPassword } = editingDevice;
        const payload = accessMode === 'agent' ? deviceWithoutPassword : editingDevice;
        try {
            if (editingDevice.id) {
                await apiService.updateNetworkDevice(editingDevice.id, payload);
                setSnackbar({ open: true, message: 'Network device updated', severity: 'success' });
            } else {
                await apiService.addNetworkDevice(payload);
                setSnackbar({ open: true, message: 'Network device added', severity: 'success' });
            }
            setEditDialogOpen(false);
            loadDevices();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error saving network device', severity: 'error' });
        }
    };

    const handleDeleteDevice = async (id) => {
        if (!window.confirm('Delete this network device?')) return;
        try {
            await apiService.deleteNetworkDevice(id);
            setSnackbar({ open: true, message: 'Network device deleted', severity: 'success' });
            loadDevices();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error deleting network device', severity: 'error' });
        }
    };

    const handleCheckNow = async (device) => {
        const seq = (checkSeqRef.current[device.id] || 0) + 1;
        checkSeqRef.current[device.id] = seq;
        activeDeviceIdRef.current = device.id;
        // True only while no newer check (on this device or any other) has
        // started since this one did -- see the refs' declaration above.
        const isCurrent = () => activeDeviceIdRef.current === device.id
            && checkSeqRef.current[device.id] === seq;

        setCheckingId(device.id);
        setHealthDevice(device);
        setHealth(null);
        setOnuResult(null);
        setHealthError(null);
        setHealthDialogOpen(true);
        try {
            const response = await apiService.checkNetworkDeviceNow(device.id);
            const { ok, message, job_id, device: updatedDevice } = response.data;
            // Keyed by device id already, so this is safe to apply
            // regardless of which check is "current" -- it can never
            // clobber another device's row.
            setDevices(prev => prev.map(d => d.id === device.id ? updatedDevice : d));
            if (!ok) {
                if (isCurrent()) setHealthError(message);
                return;
            }
            const job = await pollNetworkJob(job_id);
            if (!isCurrent()) return;
            if (job.status !== 'done' || job.error) {
                setHealthError(job.error || 'Check failed');
                return;
            }
            if (device.device_type === 'vsol_olt') {
                // For a vsol_olt, the job result is the ONU list itself --
                // there's no interface-label workflow for this device type,
                // so we don't touch labelDrafts/health at all here.
                setOnuResult(job.result || []);
            } else {
                setHealth(job.result);
                const drafts = {};
                // Defensive: degrade to no drafts rather than throwing if the
                // result shape omits interfaces.
                job.result?.interfaces?.forEach(iface => { drafts[iface.name] = iface.label || ''; });
                setLabelDrafts(drafts);
            }
        } catch (err) {
            if (isCurrent()) setHealthError(err.response?.data?.message || 'Check failed');
        } finally {
            // Only clear the spinner if no newer check has superseded this
            // one -- otherwise a slow, superseded check's finally would
            // clear the spinner for the check that's actually still
            // running (the bug this whole guard exists to prevent).
            if (isCurrent()) setCheckingId(null);
        }
    };

    const handleSaveLabel = async (interfaceName) => {
        try {
            const response = await apiService.setNetworkDeviceInterfaceLabel(healthDevice.id, {
                interface_name: interfaceName, label: labelDrafts[interfaceName] || '',
            });
            setDevices(prev => prev.map(d => d.id === healthDevice.id ? response.data.device : d));
            setSnackbar({ open: true, message: 'Label saved', severity: 'success' });
            setHealth(prev => ({
                ...prev,
                interfaces: prev.interfaces.map(iface =>
                    iface.name === interfaceName ? { ...iface, label: labelDrafts[interfaceName] || null } : iface
                ),
            }));
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error saving label', severity: 'error' });
        }
    };

    return (
        <Box sx={{ width: '100%', mb: 4 }}>
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                <Box>
                    <Typography variant="h5" sx={{ fontWeight: 600 }}>Network Devices</Typography>
                    <Typography variant="body2" color="text.secondary">Your own network hardware (e.g. a core router) — on-demand reachability and interface status.</Typography>
                </Box>
                <Button
                    variant="contained"
                    startIcon={<AddIcon />}
                    onClick={() => { setEditingDevice({ name: '', host: '', device_type: 'mikrotik_ccr', parent_device_id: '', api_port: 8728, use_tls: false, username: '', password: '', status: 'active' }); setEditDialogOpen(true); }}
                    sx={{ width: { xs: '100%', sm: 'auto' } }}
                >
                    Add Device
                </Button>
            </Box>

            {/* No agent in 'direct' mode -- rendering this would just be noise. */}
            {accessMode === 'agent' && (
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 3 }}>
                    <Chip size="small" color={agentOnline ? 'success' : 'error'}
                        label={agentOnline ? 'Agent online' : 'Agent offline'} />
                    <Typography variant="caption" color="text.secondary">
                        {agent?.last_seen_at ? `last seen ${formatStamp(agent.last_seen_at)}` : 'never connected'}
                    </Typography>
                </Stack>
            )}

            {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>
            ) : (
                <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid #e0e0e0', borderRadius: '12px' }}>
                    <Table>
                        <TableHead sx={{ bgcolor: '#f8fafc' }}>
                            <TableRow>
                                <TableCell>Name</TableCell>
                                <TableCell>Host</TableCell>
                                <TableCell>Last Check</TableCell>
                                <TableCell align="right">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {devices.map((d) => (
                                <TableRow key={d.id}>
                                    <TableCell sx={{ fontWeight: 600 }}>{d.name}</TableCell>
                                    <TableCell>
                                        {d.host}:{d.api_port}{d.use_tls ? ' (TLS)' : ''}
                                        {d.device_type === 'vsol_olt' ? ' — OLT (SNMP)' : ' — CCR (RouterOS)'}
                                    </TableCell>
                                    <TableCell>
                                        <Tooltip title={d.last_checked_at ? formatStamp(d.last_checked_at) : ''}>
                                            <Chip size="small" label={STATUS_LABEL[d.last_status || NOT_CHECKED] || d.last_status} color={STATUS_COLOR[d.last_status || NOT_CHECKED] || 'default'} />
                                        </Tooltip>
                                    </TableCell>
                                    <TableCell align="right">
                                        <Tooltip title={agentOffline ? agentOfflineReason : 'Check Now'}>
                                            <span>
                                                <IconButton color="info" onClick={() => handleCheckNow(d)} disabled={checkingId === d.id || agentOffline}>
                                                    {checkingId === d.id ? <CircularProgress size={18} /> : <CheckNowIcon fontSize="small" />}
                                                </IconButton>
                                            </span>
                                        </Tooltip>
                                        <Tooltip title="Edit">
                                            <IconButton onClick={() => { setEditingDevice({ ...d, password: '' }); setEditDialogOpen(true); }}>
                                                <EditIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Delete">
                                            <IconButton color="error" onClick={() => handleDeleteDevice(d.id)}>
                                                <DeleteIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {devices.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={4} align="center" sx={{ py: 3 }}>No network devices yet.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}

            {/* Edit/Add Device Dialog */}
            <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{editingDevice?.id ? 'Edit Network Device' : 'Add Network Device'}</DialogTitle>
                <DialogContent dividers>
                    <Grid container spacing={2}>
                        <Grid item xs={12} sm={6}>
                            <TextField fullWidth select label="Device Type"
                                value={editingDevice?.device_type || 'mikrotik_ccr'}
                                onChange={(e) => {
                                    const device_type = e.target.value;
                                    const isOlt = device_type === 'vsol_olt';
                                    setEditingDevice({
                                        ...editingDevice,
                                        device_type,
                                        api_port: isOlt ? 161 : (editingDevice?.use_tls ? 8729 : 8728),
                                        ...(isOlt ? { use_tls: false, username: '' } : {}),
                                    });
                                }}
                                SelectProps={{ native: true }}>
                                <option value="mikrotik_ccr">Mikrotik CCR (RouterOS)</option>
                                <option value="vsol_olt">V-SOL OLT (SNMP)</option>
                            </TextField>
                        </Grid>
                        <Grid item xs={12} sm={6}>
                            <TextField fullWidth select label="Connected To (upstream device)"
                                value={editingDevice?.parent_device_id ?? ''}
                                helperText="Leave as 'None' for the root device"
                                onChange={(e) => setEditingDevice({ ...editingDevice, parent_device_id: e.target.value })}
                                SelectProps={{ native: true }}>
                                <option value="">None (root)</option>
                                {devices
                                    .filter((d) => d.id !== editingDevice?.id)
                                    .map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                            </TextField>
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth label="Name" value={editingDevice?.name || ''}
                                onChange={(e) => setEditingDevice({ ...editingDevice, name: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} md={8}>
                            <TextField fullWidth label="Host (IP or hostname)" value={editingDevice?.host || ''}
                                onChange={(e) => setEditingDevice({ ...editingDevice, host: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} sm={6}>
                            <TextField fullWidth type="number"
                                label={editingDevice?.device_type === 'vsol_olt' ? 'SNMP Port' : 'API Port'}
                                value={editingDevice?.api_port ?? (editingDevice?.device_type === 'vsol_olt' ? 161 : 8728)}
                                onChange={(e) => setEditingDevice({ ...editingDevice, api_port: e.target.value })} />
                        </Grid>
                        {editingDevice?.device_type !== 'vsol_olt' && (
                            <Grid item xs={12} sm={6}>
                                <FormControlLabel label="Use TLS (API-SSL)"
                                    control={<Switch checked={!!editingDevice?.use_tls}
                                        onChange={(e) => setEditingDevice({ ...editingDevice, use_tls: e.target.checked, api_port: e.target.checked ? 8729 : 8728 })} />} />
                            </Grid>
                        )}
                        {editingDevice?.device_type !== 'vsol_olt' && (
                            <Grid item xs={12} sm={6}>
                                <TextField fullWidth label="Username" value={editingDevice?.username || ''}
                                    onChange={(e) => setEditingDevice({ ...editingDevice, username: e.target.value })} />
                            </Grid>
                        )}
                        {accessMode === 'agent' ? (
                            // The cloud never holds a device credential in agent mode -- the
                            // backend rejects a supplied password outright (see
                            // handleSaveDevice, which strips this key from the payload).
                            // The field is hidden entirely rather than shown-and-disabled so
                            // there's no empty password box inviting a keystroke.
                            <Grid item xs={12} sm={6}>
                                <Alert severity="info" sx={{ height: '100%', alignItems: 'center' }}>
                                    {editingDevice?.device_type === 'vsol_olt' ? 'The SNMP community string' : 'The password'} lives
                                    in <code>agent.toml</code> on the on-prem box, not here.
                                </Alert>
                            </Grid>
                        ) : (
                            <Grid item xs={12} sm={6}>
                                <TextField fullWidth type="password"
                                    label={editingDevice?.device_type === 'vsol_olt' ? 'SNMP Community' : 'Password'}
                                    helperText={editingDevice?.id
                                        ? (editingDevice?.device_type === 'vsol_olt' ? 'Leave blank to keep the current community string' : 'Leave blank to keep the current password')
                                        : 'Required'}
                                    value={editingDevice?.password || ''}
                                    onChange={(e) => setEditingDevice({ ...editingDevice, password: e.target.value })} />
                            </Grid>
                        )}
                        <Grid item xs={12}>
                            <TextField fullWidth select label="Status" value={editingDevice?.status || 'active'}
                                onChange={(e) => setEditingDevice({ ...editingDevice, status: e.target.value })}>
                                <MenuItem value="active">Active</MenuItem>
                                <MenuItem value="inactive">Inactive</MenuItem>
                            </TextField>
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleSaveDevice}>Save</Button>
                </DialogActions>
            </Dialog>

            {/* Check Now results dialog */}
            <Dialog open={healthDialogOpen} onClose={() => setHealthDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{healthDevice?.name} — Health Check</DialogTitle>
                <DialogContent dividers>
                    {checkingId === healthDevice?.id ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>
                    ) : healthError ? (
                        <Typography color="error">{healthError}</Typography>
                    ) : onuResult ? (
                        <Box>
                            <Typography variant="body2" sx={{ mb: 1 }}>
                                <b>{onuResult.length}</b> ONU{onuResult.length === 1 ? '' : 's'} reporting
                            </Typography>
                            <Stack direction="row" spacing={1}>
                                <Chip size="small" color="success"
                                    label={`${onuResult.filter((o) => o.status === 'online').length} online`} />
                                <Chip size="small" color="error"
                                    label={`${onuResult.filter((o) => o.status !== 'online').length} offline`} />
                            </Stack>
                        </Box>
                    ) : health ? (
                        <Box>
                            <Typography variant="body2" sx={{ mb: 1 }}><b>Identity:</b> {health.identity}</Typography>
                            <Typography variant="body2" sx={{ mb: 2 }}><b>Uptime:</b> {health.uptime}</Typography>
                            <Table size="small">
                                <TableHead>
                                    <TableRow>
                                        <TableCell>Interface</TableCell>
                                        <TableCell>Status</TableCell>
                                        <TableCell>Label</TableCell>
                                        <TableCell />
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {health.interfaces?.map((iface) => (
                                        <TableRow key={iface.name}>
                                            <TableCell>{iface.name}</TableCell>
                                            <TableCell>
                                                <Chip size="small"
                                                    label={iface.disabled ? 'Disabled' : (iface.running ? 'Running' : 'Not Running')}
                                                    color={iface.disabled ? 'default' : (iface.running ? 'success' : 'warning')} />
                                            </TableCell>
                                            <TableCell>
                                                <TextField size="small" placeholder="e.g. thglobal"
                                                    value={labelDrafts[iface.name] ?? ''}
                                                    onChange={(e) => setLabelDrafts({ ...labelDrafts, [iface.name]: e.target.value })} />
                                            </TableCell>
                                            <TableCell>
                                                <Button size="small" onClick={() => handleSaveLabel(iface.name)}>Save</Button>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </Box>
                    ) : (
                        <Typography variant="body2" color="text.secondary">No health data returned for this device.</Typography>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setHealthDialogOpen(false)}>Close</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default NetworkDeviceManagementView;
