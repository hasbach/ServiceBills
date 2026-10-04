import React, { useState, useEffect } from 'react';
import {
    Box, Typography, Button, TextField, Dialog, DialogTitle,
    DialogContent, DialogActions, Grid, Paper, TableContainer,
    Table, TableHead, TableRow, TableCell, TableBody, MenuItem,
    IconButton, Tooltip, Chip, CircularProgress, Tabs, Tab
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    Payment as PaymentIcon,
    AttachMoney as AddCreditIcon,
    MoneyOff as DiscountIcon,
    History as HistoryIcon
} from '@mui/icons-material';
import { apiService, useAppContext } from '../context/AppContext';
import BalanceLogTable from './BalanceLogTable';

const ResellerManagementView = () => {
    const { setSnackbar } = useAppContext();
    const [resellers, setResellers] = useState([]);
    const [loading, setLoading] = useState(true);

    // Dialog states
    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingReseller, setEditingReseller] = useState(null);

    const [financialDialogOpen, setFinancialDialogOpen] = useState(false);
    const [financialAction, setFinancialAction] = useState(''); // 'add_credit', 'apply_discount', 'collect_payment'
    const [financialAmount, setFinancialAmount] = useState('');
    const [selectedResellerId, setSelectedResellerId] = useState(null);
    
    const [historyDialogOpen, setHistoryDialogOpen] = useState(false);
    const [historyData, setHistoryData] = useState([]);
    const [historyLoading, setHistoryLoading] = useState(false);
    const [historyResellerId, setHistoryResellerId] = useState(null);
    const [historyTab, setHistoryTab] = useState(0);

    const openHistoryDialog = async (id) => {
        setHistoryResellerId(id);
        setHistoryTab(0);
        setHistoryDialogOpen(true);
        setHistoryLoading(true);
        try {
            const response = await apiService.getResellerHistory(id);
            setHistoryData(response.data || []);
        } catch (error) {
            console.error("Error fetching history", error);
            setSnackbar({ open: true, message: 'Failed to load history', severity: 'error' });
        } finally {
            setHistoryLoading(false);
        }
    };


    useEffect(() => {
        loadResellers();
    }, []);

    const loadResellers = async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchResellers();
            setResellers(response.data);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load resellers', severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    const handleSaveReseller = async () => {
        try {
            if (editingReseller.id) {
                await apiService.updateReseller(editingReseller.id, editingReseller);
                setSnackbar({ open: true, message: 'Reseller updated', severity: 'success' });
            } else {
                await apiService.addReseller(editingReseller);
                setSnackbar({ open: true, message: 'Reseller added', severity: 'success' });
            }
            setEditDialogOpen(false);
            loadResellers();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error saving reseller', severity: 'error' });
        }
    };

    const handleFinancialAction = async () => {
        if (!financialAmount || parseFloat(financialAmount) <= 0) {
            setSnackbar({ open: true, message: 'Please enter a valid amount', severity: 'warning' });
            return;
        }

        try {
            const payload = { amount: parseFloat(financialAmount) };
            if (financialAction === 'add_credit') {
                await apiService.addResellerCredit(selectedResellerId, payload);
                setSnackbar({ open: true, message: 'Credit added successfully', severity: 'success' });
            } else if (financialAction === 'apply_discount') {
                await apiService.applyResellerDiscount(selectedResellerId, payload);
                setSnackbar({ open: true, message: 'Discount applied successfully', severity: 'success' });
            } else if (financialAction === 'collect_payment') {
                await apiService.collectResellerPayment(selectedResellerId, payload);
                setSnackbar({ open: true, message: 'Payment collected successfully', severity: 'success' });
            }
            setFinancialDialogOpen(false);
            setFinancialAmount('');
            loadResellers();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || `Error performing ${financialAction}`, severity: 'error' });
        }
    };

    const openFinancialDialog = (id, action) => {
        setSelectedResellerId(id);
        setFinancialAction(action);
        setFinancialAmount('');
        setFinancialDialogOpen(false); // reset
        setTimeout(() => setFinancialDialogOpen(true), 10);
    };

    const getActionTitle = () => {
        if (financialAction === 'add_credit') return 'Add Credit to Reseller';
        if (financialAction === 'apply_discount') return 'Apply Discount';
        if (financialAction === 'collect_payment') return 'Collect Payment';
        return '';
    };

    return (
        <Box sx={{ width: '100%', mb: 4 }}>
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                <Typography variant="h5" sx={{ fontWeight: 600 }}>Reseller Management</Typography>
                <Button 
                    variant="contained" 
                    startIcon={<AddIcon />} 
                    onClick={() => { setEditingReseller({ name: '', phone: '', type: 'type1' }); setEditDialogOpen(true); }}
                    sx={{ width: { xs: '100%', sm: 'auto' } }}
                >
                    Add Reseller
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
                                <TableCell>Phone</TableCell>
                                <TableCell>Type</TableCell>
                                <TableCell>Balance</TableCell>
                                <TableCell align="right">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {resellers.map((r) => (
                                <TableRow key={r.id}>
                                    <TableCell sx={{ fontWeight: 600 }}>{r.name}</TableCell>
                                    <TableCell>{r.phone}</TableCell>
                                    <TableCell>
                                        <Chip 
                                            label={r.type === 'type1' ? 'Credit Only (Type 1)' : 'Managed (Type 2)'} 
                                            color={r.type === 'type1' ? 'primary' : 'secondary'} 
                                            size="small" 
                                            variant="outlined" 
                                        />
                                    </TableCell>
                                    <TableCell sx={{ fontWeight: 700, color: r.balance > 0 ? '#d32f2f' : (r.balance < 0 ? '#2e7d32' : 'inherit') }}>
                                        ${parseFloat(r.balance).toFixed(2)}
                                    </TableCell>
                                    <TableCell align="right">
                                        <Tooltip title="Add Credit">
                                            <IconButton color="info" onClick={() => openFinancialDialog(r.id, 'add_credit')}>
                                                <AddCreditIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Apply Discount">
                                            <IconButton color="warning" onClick={() => openFinancialDialog(r.id, 'apply_discount')}>
                                                <DiscountIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="View History">
                                            <IconButton color="secondary" onClick={() => openHistoryDialog(r.id)}>
                                                <HistoryIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Collect Payment">
                                            <IconButton color="success" onClick={() => openFinancialDialog(r.id, 'collect_payment')}>
                                                <PaymentIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                        <Tooltip title="Edit">
                                            <IconButton onClick={() => { setEditingReseller(r); setEditDialogOpen(true); }}>
                                                <EditIcon fontSize="small" />
                                            </IconButton>
                                        </Tooltip>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {resellers.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} align="center" sx={{ py: 3 }}>No resellers found.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}

            {/* Edit/Add Reseller Dialog */}
            <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{editingReseller?.id ? 'Edit Reseller' : 'Add Reseller'}</DialogTitle>
                <DialogContent dividers>
                    <Grid container spacing={2}>
                        <Grid item xs={12}>
                            <TextField 
                                fullWidth 
                                label="Name" 
                                value={editingReseller?.name || ''} 
                                onChange={(e) => setEditingReseller({ ...editingReseller, name: e.target.value })} 
                            />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField 
                                fullWidth 
                                label="Phone" 
                                value={editingReseller?.phone || ''} 
                                onChange={(e) => setEditingReseller({ ...editingReseller, phone: e.target.value })} 
                                helperText="Include country code, e.g., 96178812525"
                            />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField 
                                fullWidth 
                                select 
                                label="Reseller Type" 
                                value={editingReseller?.type || 'type1'} 
                                onChange={(e) => setEditingReseller({ ...editingReseller, type: e.target.value })} 
                            >
                                <MenuItem value="type1">Type 1 (Credit Only - Topups)</MenuItem>
                                <MenuItem value="type2">Type 2 (Managed Customers - Renewals)</MenuItem>
                            </TextField>
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleSaveReseller}>Save</Button>
                </DialogActions>
            </Dialog>

            {/* Financial Action Dialog */}
            <Dialog open={financialDialogOpen} onClose={() => setFinancialDialogOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>{getActionTitle()}</DialogTitle>
                <DialogContent dividers>
                    <TextField 
                        fullWidth 
                        label="Amount ($)" 
                        type="number" 
                        value={financialAmount} 
                        onChange={(e) => setFinancialAmount(e.target.value)} 
                        autoFocus
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setFinancialDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleFinancialAction}>Confirm</Button>
                </DialogActions>
            </Dialog>

            <Dialog open={historyDialogOpen} onClose={() => setHistoryDialogOpen(false)} maxWidth="md" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Reseller Financial History</DialogTitle>
                <Tabs value={historyTab} onChange={(e, v) => setHistoryTab(v)} sx={{ px: 3, borderBottom: 1, borderColor: 'divider' }}>
                    <Tab label="Transactions" />
                    <Tab label="Balance log" />
                </Tabs>
                <DialogContent dividers>
                    {historyTab === 1 ? (
                        <BalanceLogTable key={historyResellerId} load={() => apiService.getResellerBalanceLog(historyResellerId)} />
                    ) : historyLoading ? (
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
                                            <TableCell sx={{ whiteSpace: 'nowrap' }}>
                                                {row.date_is_utc
                                                    ? new Date(row.date.replace(' ', 'T') + 'Z').toLocaleString()
                                                    : row.date.slice(0, 10)}
                                            </TableCell>
                                            <TableCell>
                                                <Chip size="small" label={row.type.replace('_', ' ')} color={row.type === 'credit_added' ? 'info' : row.type === 'discount_applied' ? 'warning' : 'success'} />
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

export default ResellerManagementView;
