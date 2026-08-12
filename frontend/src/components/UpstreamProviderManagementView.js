import React, { useState, useEffect } from 'react';
import {
    Box, Typography, Button, TextField, Dialog, DialogTitle,
    DialogContent, DialogActions, Grid, Paper, TableContainer,
    Table, TableHead, TableRow, TableCell, TableBody, MenuItem,
    IconButton, Tooltip, Chip, CircularProgress
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    AttachMoney as TopupIcon,
    Receipt as RenewalCostIcon,
    History as HistoryIcon
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';

const PRODUCT_LABELS = { proradius: 'PROradius', radiusnew: 'radiusnew', manual: 'Manual' };

// Upstream RADIUS operator the tenant is a subreseller of (mode: 'upstream_bridge').
// Data-model + manual tracking only -- no portal automation exists yet, see
// docs/superpowers/specs/2026-08-12-network-enforcement-design.md, Concept A.
const UpstreamProviderManagementView = ({ customers = [] }) => {
    const { setSnackbar } = useAppContext();
    const [providers, setProviders] = useState([]);
    const [loading, setLoading] = useState(true);

    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingProvider, setEditingProvider] = useState(null);

    const [ledgerDialogOpen, setLedgerDialogOpen] = useState(false);
    const [ledgerAction, setLedgerAction] = useState(''); // 'topup' | 'renewal_cost'
    const [ledgerAmount, setLedgerAmount] = useState('');
    const [ledgerDescription, setLedgerDescription] = useState('');
    const [ledgerCustomerId, setLedgerCustomerId] = useState('');
    const [selectedProviderId, setSelectedProviderId] = useState(null);

    const [historyDialogOpen, setHistoryDialogOpen] = useState(false);
    const [historyData, setHistoryData] = useState([]);
    const [historyLoading, setHistoryLoading] = useState(false);

    useEffect(() => {
        loadProviders();
    }, []);

    const loadProviders = async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchUpstreamProviders();
            setProviders(response.data);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load upstream providers', severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    const handleSaveProvider = async () => {
        try {
            if (editingProvider.id) {
                await apiService.updateUpstreamProvider(editingProvider.id, editingProvider);
                setSnackbar({ open: true, message: 'Upstream provider updated', severity: 'success' });
            } else {
                await apiService.addUpstreamProvider(editingProvider);
                setSnackbar({ open: true, message: 'Upstream provider added', severity: 'success' });
            }
            setEditDialogOpen(false);
            loadProviders();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error saving upstream provider', severity: 'error' });
        }
    };

    const handleDeleteProvider = async (id) => {
        if (!window.confirm('Delete this upstream provider? This is only possible if no customers or ledger history are linked to it.')) return;
        try {
            await apiService.deleteUpstreamProvider(id);
            setSnackbar({ open: true, message: 'Upstream provider deleted', severity: 'success' });
            loadProviders();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error deleting upstream provider', severity: 'error' });
        }
    };

    const openLedgerDialog = (id, action) => {
        setSelectedProviderId(id);
        setLedgerAction(action);
        setLedgerAmount('');
        setLedgerDescription('');
        setLedgerCustomerId('');
        setLedgerDialogOpen(false);
        setTimeout(() => setLedgerDialogOpen(true), 10);
    };

    const handleLedgerAction = async () => {
        const amount = parseFloat(ledgerAmount);
        if (!amount || amount <= 0) {
            setSnackbar({ open: true, message: 'Please enter a valid amount', severity: 'warning' });
            return;
        }
        if (ledgerAction === 'renewal_cost' && !ledgerCustomerId) {
            setSnackbar({ open: true, message: 'Please select a customer', severity: 'warning' });
            return;
        }
        try {
            const payload = { amount, description: ledgerDescription };
            if (ledgerAction === 'topup') {
                await apiService.topupUpstreamProvider(selectedProviderId, payload);
                setSnackbar({ open: true, message: 'Top-up recorded', severity: 'success' });
            } else {
                await apiService.recordUpstreamRenewalCost(selectedProviderId, { ...payload, customer_id: ledgerCustomerId });
                setSnackbar({ open: true, message: 'Renewal cost recorded', severity: 'success' });
            }
            setLedgerDialogOpen(false);
            loadProviders();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error recording entry', severity: 'error' });
        }
    };

    const openHistoryDialog = async (id) => {
        setHistoryDialogOpen(true);
        setHistoryLoading(true);
        try {
            const response = await apiService.getUpstreamProviderHistory(id);
            setHistoryData(response.data || []);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load history', severity: 'error' });
        } finally {
            setHistoryLoading(false);
        }
    };

    return (
        <Box sx={{ width: '100%', mb: 4 }}>
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                <Box>
                    <Typography variant="h5" sx={{ fontWeight: 600 }}>Upstream Providers</Typography>
                    <Typography variant="body2" color="text.secondary">The RADIUS operator(s) you're a subreseller of — manual balance &amp; renewal-cost tracking, no portal automation yet.</Typography>
                </Box>
                <Button
                    variant="contained"
                    startIcon={<AddIcon />}
                    onClick={() => { setEditingProvider({ name: '', product: 'manual', portal_url: '', portal_username: '', portal_password: '', status: 'active', balance: 0 }); setEditDialogOpen(true); }}
                    sx={{ width: { xs: '100%', sm: 'auto' } }}
                >
                    Add Provider
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
                                <TableCell>Product</TableCell>
                                <TableCell>Balance</TableCell>
                                <TableCell>Status</TableCell>
                                <TableCell align="right">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {providers.map((p) => (
                                <TableRow key={p.id}>
                                    <TableCell sx={{ fontWeight: 600 }}>{p.name}</TableCell>
                                    <TableCell>
                                        <Chip label={PRODUCT_LABELS[p.product] || p.product} size="small" variant="outlined" />
                                    </TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>${parseFloat(p.balance).toFixed(2)}</TableCell>
                                    <TableCell>
                                        <Chip label={p.status} size="small" color={p.status === 'active' ? 'success' : 'default'} />
                                    </TableCell>
                                    <TableCell align="right">
                                        <Tooltip title="Record Balance Top-up">
                                            <IconButton color="info" onClick={() => openLedgerDialog(p.id, 'topup')}>
                                                <TopupIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Record Renewal Cost">
                                            <IconButton color="warning" onClick={() => openLedgerDialog(p.id, 'renewal_cost')}>
                                                <RenewalCostIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="View History">
                                            <IconButton color="secondary" onClick={() => openHistoryDialog(p.id)}>
                                                <HistoryIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Edit">
                                            <IconButton onClick={() => { setEditingProvider(p); setEditDialogOpen(true); }}>
                                                <EditIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Delete">
                                            <IconButton color="error" onClick={() => handleDeleteProvider(p.id)}>
                                                <DeleteIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {providers.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} align="center" sx={{ py: 3 }}>No upstream providers yet.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}

            {/* Edit/Add Provider Dialog */}
            <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{editingProvider?.id ? 'Edit Upstream Provider' : 'Add Upstream Provider'}</DialogTitle>
                <DialogContent dividers>
                    <Grid container spacing={2}>
                        <Grid item xs={12}>
                            <TextField fullWidth label="Name" value={editingProvider?.name || ''}
                                onChange={(e) => setEditingProvider({ ...editingProvider, name: e.target.value })} />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth select label="Product" value={editingProvider?.product || 'manual'}
                                onChange={(e) => setEditingProvider({ ...editingProvider, product: e.target.value })}>
                                <MenuItem value="manual">Manual (not yet classified / no automation planned)</MenuItem>
                                <MenuItem value="proradius">PROradius</MenuItem>
                                <MenuItem value="radiusnew">radiusnew</MenuItem>
                            </TextField>
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth label="Portal URL (Optional)" value={editingProvider?.portal_url || ''}
                                onChange={(e) => setEditingProvider({ ...editingProvider, portal_url: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth label="Portal Username (Optional)" value={editingProvider?.portal_username || ''}
                                onChange={(e) => setEditingProvider({ ...editingProvider, portal_username: e.target.value })} />
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth type="password" label="Portal Password (Optional)"
                                helperText={editingProvider?.id ? 'Leave blank to keep the current password' : 'Stored encrypted; unused until automation ships'}
                                value={editingProvider?.portal_password || ''}
                                onChange={(e) => setEditingProvider({ ...editingProvider, portal_password: e.target.value })} />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth select label="Status" value={editingProvider?.status || 'active'}
                                onChange={(e) => setEditingProvider({ ...editingProvider, status: e.target.value })}>
                                <MenuItem value="active">Active</MenuItem>
                                <MenuItem value="inactive">Inactive</MenuItem>
                            </TextField>
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleSaveProvider}>Save</Button>
                </DialogActions>
            </Dialog>

            {/* Top-up / Renewal-cost Dialog */}
            <Dialog open={ledgerDialogOpen} onClose={() => setLedgerDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{ledgerAction === 'topup' ? 'Record Balance Top-up' : 'Record Renewal Cost'}</DialogTitle>
                <DialogContent dividers>
                    <Grid container spacing={2}>
                        {ledgerAction === 'renewal_cost' && (
                            <Grid item xs={12}>
                                <TextField fullWidth select label="Customer" value={ledgerCustomerId}
                                    onChange={(e) => setLedgerCustomerId(e.target.value)}>
                                    <MenuItem value="">Select a customer</MenuItem>
                                    {customers.map(c => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
                                </TextField>
                            </Grid>
                        )}
                        <Grid item xs={12}>
                            <TextField fullWidth label="Amount ($)" type="number" value={ledgerAmount}
                                onChange={(e) => setLedgerAmount(e.target.value)} autoFocus />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField fullWidth label="Description (Optional)" value={ledgerDescription}
                                onChange={(e) => setLedgerDescription(e.target.value)} />
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setLedgerDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleLedgerAction}>Confirm</Button>
                </DialogActions>
            </Dialog>

            {/* History Dialog */}
            <Dialog open={historyDialogOpen} onClose={() => setHistoryDialogOpen(false)} maxWidth="md" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Provider Ledger History</DialogTitle>
                <DialogContent dividers>
                    {historyLoading ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>
                    ) : historyData.length === 0 ? (
                        <Typography sx={{ textAlign: 'center', color: 'text.secondary', p: 4 }}>No history records found.</Typography>
                    ) : (
                        <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid #eee' }}>
                            <Table size="small">
                                <TableHead>
                                    <TableRow sx={{ backgroundColor: '#f5f5f5' }}>
                                        <TableCell sx={{ fontWeight: 'bold' }}>Date</TableCell>
                                        <TableCell sx={{ fontWeight: 'bold' }}>Type</TableCell>
                                        <TableCell sx={{ fontWeight: 'bold' }}>Amount</TableCell>
                                        <TableCell sx={{ fontWeight: 'bold' }}>Description</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {historyData.map(row => (
                                        <TableRow key={row.id}>
                                            <TableCell>{new Date(row.date).toLocaleString()}</TableCell>
                                            <TableCell>
                                                <Chip size="small" label={row.type.replace('_', ' ')}
                                                    color={row.type === 'balance_topup' ? 'info' : row.type === 'renewal_cost' ? 'warning' : 'default'} />
                                            </TableCell>
                                            <TableCell sx={{ fontWeight: 600 }}>${parseFloat(row.amount).toFixed(2)}</TableCell>
                                            <TableCell>{row.description}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setHistoryDialogOpen(false)}>Close</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default UpstreamProviderManagementView;
