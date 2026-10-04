import React, { useState, useEffect } from 'react';
import {
    Box, Typography, Paper, Button, CircularProgress,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
    IconButton, Tooltip, Chip, alpha, useTheme, Tabs, Tab
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    Payment as PaymentIcon,
    AccountBalance as BalanceIcon,
    History as HistoryIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext';
import BalanceLogTable from './BalanceLogTable';

function SuppliersView() {
    const theme = useTheme();
    const { apiService, setSnackbar, user } = useAppContext();
    // Correcting a recorded payment (edit/delete) is admin-only -- also
    // enforced by the backend (_admin_only_error in app.py).
    const isAdmin = (user?.role || '').split(',').map(r => r.trim().toLowerCase()).includes('admin');
    const [editPayment, setEditPayment] = useState(null); // { payment_id, amount, payment_date, payment_method, reference_note }
    const [suppliers, setSuppliers] = useState([]);
    const [loading, setLoading] = useState(true);

    const [supplierDialog, setSupplierDialog] = useState({ open: false, data: null });
    const [paymentDialog, setPaymentDialog] = useState({ open: false, supplierId: null, amount: '' });
    const [historyDialog, setHistoryDialog] = useState({ open: false, supplier: null, history: [] });
    const [historyTab, setHistoryTab] = useState(0);
    const [fixBalanceInput, setFixBalanceInput] = useState('');
    const [historyLoading, setHistoryLoading] = useState(false);

    const loadSuppliers = () => {
        setLoading(true);
        apiService.fetchSuppliers()
            .then(res => setSuppliers(res.data))
            .catch(err => setSnackbar({ open: true, message: 'Failed to load suppliers.', severity: 'error' }))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        loadSuppliers();
    }, []);

    const handleSaveSupplier = async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const data = Object.fromEntries(formData.entries());

        try {
            if (supplierDialog.data) {
                await apiService.updateSupplier(supplierDialog.data.id, data);
                setSnackbar({ open: true, message: 'Supplier updated.', severity: 'success' });
            } else {
                await apiService.addSupplier(data);
                setSnackbar({ open: true, message: 'Supplier created.', severity: 'success' });
            }
            setSupplierDialog({ open: false, data: null });
            loadSuppliers();
        } catch (error) {
            setSnackbar({ open: true, message: 'Error saving supplier.', severity: 'error' });
        }
    };

    const handleDeleteSupplier = async (id) => {
        if (!window.confirm("Are you sure you want to delete this supplier?")) return;
        try {
            await apiService.deleteSupplier(id);
            setSnackbar({ open: true, message: 'Supplier deleted.', severity: 'success' });
            loadSuppliers();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Error deleting supplier.', severity: 'error' });
        }
    };

    const handleRecordPayment = async (e) => {
        e.preventDefault();
        try {
            await apiService.recordSupplierPayment(paymentDialog.supplierId, { amount: paymentDialog.amount });
            setSnackbar({ open: true, message: 'Payment recorded successfully.', severity: 'success' });
            setPaymentDialog({ open: false, supplierId: null, amount: '' });
            loadSuppliers();
        } catch (error) {
            setSnackbar({ open: true, message: 'Error recording payment.', severity: 'error' });
        }
    };
    const handleOpenHistory = async (supplier) => {
        setHistoryDialog({ open: true, supplier, history: [] });
        if (historyDialog.supplier?.id !== supplier.id) setHistoryTab(0);
        setFixBalanceInput(supplier.balance);
        setHistoryLoading(true);
        try {
            const res = await apiService.fetchSupplierHistory(supplier.id);
            setHistoryDialog({ open: true, supplier: res.data.supplier, history: res.data.history });
            setFixBalanceInput(res.data.supplier.balance);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load supplier history.', severity: 'error' });
        } finally {
            setHistoryLoading(false);
        }
    };

    const handleFixBalance = async () => {
        if (!historyDialog.supplier) return;
        try {
            const res = await apiService.fixSupplierBalance(historyDialog.supplier.id, { balance: fixBalanceInput });
            setSnackbar({ open: true, message: 'Balance updated successfully.', severity: 'success' });
            setHistoryDialog({ ...historyDialog, supplier: res.data.supplier });
            loadSuppliers();
        } catch (err) {
            setSnackbar({ open: true, message: 'Error updating balance.', severity: 'error' });
        }
    };

    const handleSaveEditedPayment = async (e) => {
        e.preventDefault();
        if (!historyDialog.supplier || !editPayment) return;
        try {
            const payload = {
                amount: editPayment.amount,
                payment_method: editPayment.payment_method,
                reference_note: editPayment.reference_note,
            };
            // Only send the date when it was changed: the field is day-only,
            // so re-sending it would reset the payment's time to midnight.
            if (editPayment.payment_date && editPayment.payment_date !== editPayment.original_date) {
                payload.payment_date = editPayment.payment_date;
            }
            await apiService.updateSupplierPayment(historyDialog.supplier.id, editPayment.payment_id, payload);
            setSnackbar({ open: true, message: 'Payment updated.', severity: 'success' });
            setEditPayment(null);
            handleOpenHistory(historyDialog.supplier);
            loadSuppliers();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error updating payment.', severity: 'error' });
        }
    };

    const handleDeletePayment = async (h) => {
        if (!historyDialog.supplier) return;
        if (!window.confirm(`Delete this payment of $${Math.abs(h.amount).toFixed(2)}? The amount will be added back to the balance owed.`)) return;
        try {
            await apiService.deleteSupplierPayment(historyDialog.supplier.id, h.payment_id);
            setSnackbar({ open: true, message: 'Payment deleted.', severity: 'success' });
            handleOpenHistory(historyDialog.supplier);
            loadSuppliers();
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.error || 'Error deleting payment.', severity: 'error' });
        }
    };

    if (loading) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress /></Box>;

    return (
        <Box sx={{ maxWidth: 1000, mx: 'auto', p: 2 }}>
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                <Typography variant="h5" sx={{ fontWeight: 600 }}>Supplier Management</Typography>
                <Button variant="contained" startIcon={<AddIcon />} onClick={() => setSupplierDialog({ open: true, data: null })} sx={{ width: { xs: '100%', sm: 'auto' } }}>
                    Add Supplier
                </Button>
            </Box>

            <TableContainer component={Paper} elevation={2} sx={{ borderRadius: 2 }}>
                <Table>
                    <TableHead sx={{ bgcolor: alpha(theme.palette.primary.main, 0.05) }}>
                        <TableRow>
                            <TableCell><b>Name</b></TableCell>
                            <TableCell><b>Phone</b></TableCell>
                            <TableCell><b>Balance Owed</b></TableCell>
                            <TableCell align="right"><b>Actions</b></TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {suppliers.map((s) => (
                            <TableRow key={s.id}>
                                <TableCell>{s.name}</TableCell>
                                <TableCell>{s.phone}</TableCell>
                                <TableCell>
                                    <Chip 
                                        icon={<BalanceIcon />} 
                                        label={`$${s.balance.toFixed(2)}`} 
                                        color={s.balance > 0 ? 'error' : 'success'} 
                                        variant="outlined" 
                                    />
                                </TableCell>
                                <TableCell align="right">
                                    <Tooltip title="Action History">
                                        <IconButton color="info" onClick={() => handleOpenHistory(s)}>
                                            <HistoryIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Record Payment">
                                        <IconButton color="success" onClick={() => setPaymentDialog({ open: true, supplierId: s.id, amount: '' })}>
                                            <PaymentIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Edit">
                                        <IconButton color="primary" onClick={() => setSupplierDialog({ open: true, data: s })}>
                                            <EditIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Delete">
                                        <IconButton color="error" onClick={() => handleDeleteSupplier(s.id)}>
                                            <DeleteIcon />
                                        </IconButton>
                                    </Tooltip>
                                </TableCell>
                            </TableRow>
                        ))}
                        {suppliers.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={4} align="center" sx={{ py: 3, color: 'text.secondary' }}>No suppliers found.</TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </TableContainer>

            {/* Supplier Form Dialog */}
            <Dialog open={supplierDialog.open} onClose={() => setSupplierDialog({ open: false, data: null })} maxWidth="sm" fullWidth>
                <form onSubmit={handleSaveSupplier}>
                    <DialogTitle>{supplierDialog.data ? 'Edit Supplier' : 'Add Supplier'}</DialogTitle>
                    <DialogContent dividers>
                        <TextField fullWidth margin="dense" name="name" label="Supplier Name" defaultValue={supplierDialog.data?.name || ''} required />
                        <TextField fullWidth margin="dense" name="phone" label="Phone" defaultValue={supplierDialog.data?.phone || ''} />
                        <TextField fullWidth margin="dense" name="address" label="Address" defaultValue={supplierDialog.data?.address || ''} />
                        <TextField fullWidth margin="dense" name="notes" label="Notes" multiline rows={3} defaultValue={supplierDialog.data?.notes || ''} />
                        <TextField fullWidth margin="dense" name="balance" label="Fixed Credit Amount / Balance ($)" type="number" inputProps={{ step: "0.01" }} defaultValue={supplierDialog.data?.balance !== undefined ? supplierDialog.data.balance : ''} />
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={() => setSupplierDialog({ open: false, data: null })}>Cancel</Button>
                        <Button type="submit" variant="contained">Save</Button>
                    </DialogActions>
                </form>
            </Dialog>

            {/* Record Payment Dialog */}
            <Dialog open={paymentDialog.open} onClose={() => setPaymentDialog({ open: false, supplierId: null, amount: '' })} maxWidth="xs" fullWidth>
                <form onSubmit={handleRecordPayment}>
                    <DialogTitle>Record Payment</DialogTitle>
                    <DialogContent dividers>
                        <Typography variant="body2" sx={{ mb: 2 }}>
                            Enter the amount you paid to this supplier. This will reduce your balance owed.
                        </Typography>
                        <TextField
                            fullWidth autoFocus margin="dense" name="amount" label="Amount Paid" type="number"
                            value={paymentDialog.amount} onChange={e => setPaymentDialog({ ...paymentDialog, amount: e.target.value })}
                            inputProps={{ step: "0.01", min: "0.01" }} required
                        />
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={() => setPaymentDialog({ open: false, supplierId: null, amount: '' })}>Cancel</Button>
                        <Button type="submit" variant="contained" color="success">Record Payment</Button>
                    </DialogActions>
                </form>
            </Dialog>

            {/* Action History & Fixed Credit Amount Dialog */}
            <Dialog open={historyDialog.open} onClose={() => setHistoryDialog({ open: false, supplier: null, history: [] })} maxWidth="md" fullWidth>
                <DialogTitle>Supplier Action History & Credit Management</DialogTitle>
                <DialogContent dividers>
                    {historyDialog.supplier && (
                        <Box sx={{ mb: 3, p: 2, bgcolor: alpha(theme.palette.primary.main, 0.05), borderRadius: 2 }}>
                            <Typography variant="subtitle1" fontWeight={600}>{historyDialog.supplier.name}</Typography>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 1, flexWrap: 'wrap' }}>
                                <Typography variant="body2">Fixed Credit Balance:</Typography>
                                <TextField
                                    size="small"
                                    label="Amount ($)"
                                    type="number"
                                    inputProps={{ step: "0.01" }}
                                    value={fixBalanceInput}
                                    onChange={(e) => setFixBalanceInput(e.target.value)}
                                    sx={{ width: 180 }}
                                />
                                <Button variant="contained" size="small" onClick={handleFixBalance}>
                                    Set Fixed Credit
                                </Button>
                            </Box>
                        </Box>
                    )}

                    <Tabs value={historyTab} onChange={(e, v) => setHistoryTab(v)} sx={{ mb: 1, borderBottom: 1, borderColor: 'divider' }}>
                        <Tab label="Transactions" />
                        <Tab label="Balance log" />
                    </Tabs>

                    {historyTab === 1 ? (
                        historyDialog.supplier && (
                            <BalanceLogTable
                                // Re-mounts (refetches) whenever the balance changes from this dialog.
                                key={`${historyDialog.supplier.id}:${historyDialog.supplier.balance}:${historyDialog.history.length}`}
                                load={() => apiService.getSupplierBalanceLog(historyDialog.supplier.id)}
                            />
                        )
                    ) : historyLoading ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>
                    ) : (
                        <TableContainer>
                            <Table size="small">
                                <TableHead>
                                    <TableRow>
                                        <TableCell><b>Date</b></TableCell>
                                        <TableCell><b>Type</b></TableCell>
                                        <TableCell><b>Items Bought / Description</b></TableCell>
                                        <TableCell align="right"><b>Amount</b></TableCell>
                                        {isAdmin && <TableCell align="right"><b>Actions</b></TableCell>}
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {historyDialog.history.map((h) => (
                                        <TableRow key={h.id}>
                                            <TableCell>{h.date}</TableCell>
                                            <TableCell>
                                                <Chip
                                                    size="small"
                                                    label={h.type === 'credit_purchase' ? 'Credit Purchase' : 'Payment'}
                                                    color={h.type === 'credit_purchase' ? 'warning' : 'success'}
                                                />
                                            </TableCell>
                                            <TableCell>{h.description}</TableCell>
                                            <TableCell align="right" sx={{ fontWeight: 600, color: h.amount > 0 ? 'error.main' : 'success.main' }}>
                                                {h.amount > 0 ? `+$${h.amount.toFixed(2)}` : `-$${Math.abs(h.amount).toFixed(2)}`}
                                            </TableCell>
                                            {isAdmin && (
                                                <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                                                    {h.type === 'payment' && h.payment_id && (
                                                        <>
                                                            <Tooltip title="Edit payment">
                                                                <IconButton size="small" color="primary" onClick={() => setEditPayment({
                                                                    payment_id: h.payment_id,
                                                                    amount: Math.abs(h.amount),
                                                                    payment_date: (h.date || '').slice(0, 10),
                                                                    original_date: (h.date || '').slice(0, 10),
                                                                    payment_method: h.payment_method || '',
                                                                    reference_note: h.reference_note || '',
                                                                })}>
                                                                    <EditIcon fontSize="small" />
                                                                </IconButton>
                                                            </Tooltip>
                                                            <Tooltip title="Delete payment">
                                                                <IconButton size="small" color="error" onClick={() => handleDeletePayment(h)}>
                                                                    <DeleteIcon fontSize="small" />
                                                                </IconButton>
                                                            </Tooltip>
                                                        </>
                                                    )}
                                                </TableCell>
                                            )}
                                        </TableRow>
                                    ))}
                                    {historyDialog.history.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={isAdmin ? 5 : 4} align="center" sx={{ py: 3, color: 'text.secondary' }}>No action history found.</TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setHistoryDialog({ open: false, supplier: null, history: [] })}>Close</Button>
                </DialogActions>
            </Dialog>

            {/* Edit Supplier Payment Dialog (admin only) */}
            <Dialog open={Boolean(editPayment)} onClose={() => setEditPayment(null)} maxWidth="xs" fullWidth>
                <form onSubmit={handleSaveEditedPayment}>
                    <DialogTitle>Edit Payment</DialogTitle>
                    <DialogContent dividers>
                        <Typography variant="body2" sx={{ mb: 2 }}>
                            Changing the amount adjusts the supplier's balance owed by the difference.
                        </Typography>
                        <TextField fullWidth margin="dense" label="Amount Paid" type="number" required
                            inputProps={{ step: "0.01", min: "0.01" }}
                            value={editPayment?.amount ?? ''} onChange={e => setEditPayment({ ...editPayment, amount: e.target.value })} />
                        <TextField fullWidth margin="dense" label="Payment Date" type="date" InputLabelProps={{ shrink: true }}
                            value={editPayment?.payment_date || ''} onChange={e => setEditPayment({ ...editPayment, payment_date: e.target.value })} />
                        <TextField fullWidth margin="dense" label="Payment Method"
                            value={editPayment?.payment_method || ''} onChange={e => setEditPayment({ ...editPayment, payment_method: e.target.value })} />
                        <TextField fullWidth margin="dense" label="Note" multiline rows={2}
                            value={editPayment?.reference_note || ''} onChange={e => setEditPayment({ ...editPayment, reference_note: e.target.value })} />
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={() => setEditPayment(null)}>Cancel</Button>
                        <Button type="submit" variant="contained">Save</Button>
                    </DialogActions>
                </form>
            </Dialog>
        </Box>
    );
}

export default SuppliersView;
