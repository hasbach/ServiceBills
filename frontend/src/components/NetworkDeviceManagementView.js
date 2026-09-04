import React, { useState, useEffect } from 'react';
import {
    Box, Typography, Button, TextField, Dialog, DialogTitle,
    DialogContent, DialogActions, Grid, Paper, TableContainer,
    Table, TableHead, TableRow, TableCell, TableBody, MenuItem,
    IconButton, Tooltip, Chip, CircularProgress, Switch, FormControlLabel
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    NetworkCheck as CheckNowIcon
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';

const STATUS_COLOR = { online: 'success', unreachable: 'error', auth_failed: 'warning' };
const STATUS_LABEL = { online: 'Online', unreachable: 'Unreachable', auth_failed: 'Auth Failed' };

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
    const [healthError, setHealthError] = useState(null);
    const [labelDrafts, setLabelDrafts] = useState({});

    useEffect(() => {
        loadDevices();
    }, []);

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
        if (!editingDevice.id && !editingDevice.password) {
            setSnackbar({ open: true, message: 'Password is required', severity: 'warning' });
            return;
        }
        try {
            if (editingDevice.id) {
                await apiService.updateNetworkDevice(editingDevice.id, editingDevice);
                setSnackbar({ open: true, message: 'Network device updated', severity: 'success' });
            } else {
                await apiService.addNetworkDevice(editingDevice);
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
        setCheckingId(device.id);
        setHealthDevice(device);
        setHealth(null);
        setHealthError(null);
        setHealthDialogOpen(true);
        try {
            const response = await apiService.checkNetworkDeviceNow(device.id);
            const { ok, message, health: healthResult, device: updatedDevice } = response.data;
            setDevices(prev => prev.map(d => d.id === device.id ? updatedDevice : d));
            if (ok) {
                setHealth(healthResult);
                const drafts = {};
                healthResult.interfaces.forEach(iface => { drafts[iface.name] = iface.label || ''; });
                setLabelDrafts(drafts);
            } else {
                setHealthError(message);
            }
        } catch (err) {
            setHealthError(err.response?.data?.message || 'Check failed');
        } finally {
            setCheckingId(null);
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
                                        {d.last_status ? (
                                            <Tooltip title={d.last_checked_at || ''}>
                                                <Chip size="small" label={STATUS_LABEL[d.last_status] || d.last_status} color={STATUS_COLOR[d.last_status] || 'default'} />
                                            </Tooltip>
                                        ) : (
                                            <Typography variant="body2" color="text.secondary">Never checked</Typography>
                                        )}
                                    </TableCell>
                                    <TableCell align="right">
                                        <Tooltip title="Check Now">
                                            <IconButton color="info" onClick={() => handleCheckNow(d)} disabled={checkingId === d.id}>
                                                {checkingId === d.id ? <CircularProgress size={18} /> : <CheckNowIcon fontSize="small" />}
                                            </IconButton>
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
                        <Grid item xs={12} sm={6}>
                            <TextField fullWidth type="password"
                                label={editingDevice?.device_type === 'vsol_olt' ? 'SNMP Community' : 'Password'}
                                helperText={editingDevice?.id
                                    ? (editingDevice?.device_type === 'vsol_olt' ? 'Leave blank to keep the current community string' : 'Leave blank to keep the current password')
                                    : 'Required'}
                                value={editingDevice?.password || ''}
                                onChange={(e) => setEditingDevice({ ...editingDevice, password: e.target.value })} />
                        </Grid>
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
                                    {health.interfaces.map((iface) => (
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
                    ) : null}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setHealthDialogOpen(false)}>Close</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default NetworkDeviceManagementView;
