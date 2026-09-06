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
    Wifi as TestConnectionIcon
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';
import { formatStamp } from './formatStamp';

const STATUS_COLOR = { online: 'success', unreachable: 'error', auth_failed: 'warning' };
const STATUS_LABEL = { online: 'Online', unreachable: 'Unreachable', auth_failed: 'Auth Failed' };

// A Mikrotik router the tenant owns, running its own local PPPoE server
// (mode: 'local_mikrotik') -- live RouterOS API integration, see
// docs/superpowers/specs/2026-08-12-network-enforcement-design.md, Concept B.
const MikrotikServerManagementView = () => {
    const { setSnackbar } = useAppContext();
    const [servers, setServers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [testingId, setTestingId] = useState(null);

    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingServer, setEditingServer] = useState(null);

    useEffect(() => {
        loadServers();
    }, []);

    const loadServers = async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchMikrotikServers();
            setServers(response.data);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load Mikrotik servers', severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    const handleSaveServer = async () => {
        if (!editingServer.id && !editingServer.password) {
            setSnackbar({ open: true, message: 'Password is required', severity: 'warning' });
            return;
        }
        try {
            if (editingServer.id) {
                await apiService.updateMikrotikServer(editingServer.id, editingServer);
                setSnackbar({ open: true, message: 'Mikrotik server updated', severity: 'success' });
            } else {
                await apiService.addMikrotikServer(editingServer);
                setSnackbar({ open: true, message: 'Mikrotik server added', severity: 'success' });
            }
            setEditDialogOpen(false);
            loadServers();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error saving Mikrotik server', severity: 'error' });
        }
    };

    const handleDeleteServer = async (id) => {
        if (!window.confirm('Delete this Mikrotik server? This is only possible if no customers are linked to it.')) return;
        try {
            await apiService.deleteMikrotikServer(id);
            setSnackbar({ open: true, message: 'Mikrotik server deleted', severity: 'success' });
            loadServers();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error deleting Mikrotik server', severity: 'error' });
        }
    };

    const handleTestConnection = async (id) => {
        setTestingId(id);
        try {
            const response = await apiService.testMikrotikConnection(id);
            const { ok, message, server } = response.data;
            setSnackbar({ open: true, message, severity: ok ? 'success' : 'error' });
            setServers(prev => prev.map(s => s.id === id ? server : s));
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.message || 'Connection test failed', severity: 'error' });
        } finally {
            setTestingId(null);
        }
    };

    return (
        <Box sx={{ width: '100%', mb: 4 }}>
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                <Box>
                    <Typography variant="h5" sx={{ fontWeight: 600 }}>Mikrotik Servers</Typography>
                    <Typography variant="body2" color="text.secondary">Your own routers running local PPPoE — used to check status and suspend/unsuspend a customer's connection.</Typography>
                </Box>
                <Button
                    variant="contained"
                    startIcon={<AddIcon />}
                    onClick={() => { setEditingServer({ name: '', host: '', api_port: 8728, use_tls: false, username: '', password: '', service_name: '', status: 'active' }); setEditDialogOpen(true); }}
                    sx={{ width: { xs: '100%', sm: 'auto' } }}
                >
                    Add Server
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
                                <TableCell>Service Name</TableCell>
                                <TableCell>Last Check</TableCell>
                                <TableCell align="right">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {servers.map((s) => (
                                <TableRow key={s.id}>
                                    <TableCell sx={{ fontWeight: 600 }}>{s.name}</TableCell>
                                    <TableCell>{s.host}:{s.api_port}{s.use_tls ? ' (TLS)' : ''}</TableCell>
                                    <TableCell>{s.service_name || <Typography component="span" variant="body2" color="text.secondary">— any —</Typography>}</TableCell>
                                    <TableCell>
                                        {s.last_status ? (
                                            <Tooltip title={s.last_checked_at ? formatStamp(s.last_checked_at) : ''}>
                                                <Chip size="small" label={STATUS_LABEL[s.last_status] || s.last_status} color={STATUS_COLOR[s.last_status] || 'default'} />
                                            </Tooltip>
                                        ) : (
                                            <Typography variant="body2" color="text.secondary">Never checked</Typography>
                                        )}
                                    </TableCell>
                                    <TableCell align="right">
                                        <Tooltip title="Test Connection">
                                            <IconButton color="info" onClick={() => handleTestConnection(s.id)} disabled={testingId === s.id}>
                                                {testingId === s.id ? <CircularProgress size={18} /> : <TestConnectionIcon fontSize="small" />}
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Edit">
                                            <IconButton onClick={() => { setEditingServer({ ...s, password: '' }); setEditDialogOpen(true); }}>
                                                <EditIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Delete">
                                            <IconButton color="error" onClick={() => handleDeleteServer(s.id)}>
                                                <DeleteIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {servers.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} align="center" sx={{ py: 3 }}>No Mikrotik servers yet.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}

            {/* Edit/Add Server Dialog */}
            <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{editingServer?.id ? 'Edit Mikrotik Server' : 'Add Mikrotik Server'}</DialogTitle>
                <DialogContent dividers>
                    <Grid container spacing={2}>
                        <Grid item xs={12}>
                            <TextField fullWidth label="Name" value={editingServer?.name || ''}
                                onChange={(e) => setEditingServer({ ...editingServer, name: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} md={8}>
                            <TextField fullWidth label="Host (IP or hostname)" value={editingServer?.host || ''}
                                onChange={(e) => setEditingServer({ ...editingServer, host: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} md={4}>
                            <TextField fullWidth type="number" label="API Port" value={editingServer?.api_port ?? 8728}
                                onChange={(e) => setEditingServer({ ...editingServer, api_port: e.target.value })} />
                        </Grid>
                        <Grid item xs={12}>
                            <FormControlLabel
                                control={<Switch checked={!!editingServer?.use_tls}
                                    onChange={(e) => setEditingServer({ ...editingServer, use_tls: e.target.checked, api_port: e.target.checked ? 8729 : 8728 })} />}
                                label="Use TLS (RouterOS API-SSL, port 8729)"
                            />
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth label="Username" value={editingServer?.username || ''}
                                onChange={(e) => setEditingServer({ ...editingServer, username: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth type="password" label="Password"
                                helperText={editingServer?.id ? 'Leave blank to keep the current password' : 'Required'}
                                value={editingServer?.password || ''}
                                onChange={(e) => setEditingServer({ ...editingServer, password: e.target.value })} />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth label="Service Name (Optional)" value={editingServer?.service_name || ''}
                                helperText="Only needed if this router's network is shared with another ISP -- disambiguates PPPoE usernames that could otherwise collide with theirs. Leave blank if not applicable."
                                onChange={(e) => setEditingServer({ ...editingServer, service_name: e.target.value })} />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth select label="Status" value={editingServer?.status || 'active'}
                                onChange={(e) => setEditingServer({ ...editingServer, status: e.target.value })}>
                                <MenuItem value="active">Active</MenuItem>
                                <MenuItem value="inactive">Inactive</MenuItem>
                            </TextField>
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleSaveServer}>Save</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default MikrotikServerManagementView;
