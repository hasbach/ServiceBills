import React, { useState, useEffect } from 'react';
import {
    Box, Typography, Paper, Button, CircularProgress,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
    IconButton, Tooltip, Chip, alpha, useTheme,
    FormControlLabel, Switch, FormControl, InputLabel, Select, MenuItem
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    Payment as PaymentIcon,
    AccountBalance as BalanceIcon,
    History as HistoryIcon,
    TrendingUp as BonusIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext';

const emptyEmployeeForm = { name: '', monthly_salary: '', hire_date: '', active: true, notes: '' };
const emptyChargeForm = { type: 'bonus', amount: '', reason: '' };
const emptyPaymentForm = { amount: '', method: '', is_advance: false, note: '' };

function EmployeesView() {
    const theme = useTheme();
    const { apiService, setSnackbar } = useAppContext();
    const [employees, setEmployees] = useState([]);
    const [loading, setLoading] = useState(true);

    const [employeeDialog, setEmployeeDialog] = useState({ open: false, data: null });
    const [employeeForm, setEmployeeForm] = useState(emptyEmployeeForm);

    const [chargeDialog, setChargeDialog] = useState({ open: false, employeeId: null });
    const [chargeForm, setChargeForm] = useState(emptyChargeForm);

    const [paymentDialog, setPaymentDialog] = useState({ open: false, employeeId: null });
    const [paymentForm, setPaymentForm] = useState(emptyPaymentForm);

    const [historyDialog, setHistoryDialog] = useState({ open: false, employee: null, history: [] });
    const [fixBalanceInput, setFixBalanceInput] = useState('');
    const [historyLoading, setHistoryLoading] = useState(false);

    const loadEmployees = () => {
        setLoading(true);
        apiService.fetchEmployees()
            .then(res => setEmployees(res.data))
            .catch(() => setSnackbar({ open: true, message: 'Failed to load employees.', severity: 'error' }))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        loadEmployees();
    }, []);

    const openEmployeeDialog = (data) => {
        setEmployeeForm(data ? {
            name: data.name || '',
            monthly_salary: data.monthly_salary ?? '',
            hire_date: data.hire_date || '',
            active: data.active,
            notes: data.notes || ''
        } : emptyEmployeeForm);
        setEmployeeDialog({ open: true, data });
    };

    const handleSaveEmployee = async (e) => {
        e.preventDefault();
        try {
            if (employeeDialog.data) {
                await apiService.updateEmployee(employeeDialog.data.id, employeeForm);
                setSnackbar({ open: true, message: 'Employee updated.', severity: 'success' });
            } else {
                await apiService.addEmployee(employeeForm);
                setSnackbar({ open: true, message: 'Employee created.', severity: 'success' });
            }
            setEmployeeDialog({ open: false, data: null });
            loadEmployees();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Error saving employee.', severity: 'error' });
        }
    };

    const handleDeleteEmployee = async (id) => {
        if (!window.confirm("Are you sure you want to delete this employee?")) return;
        try {
            await apiService.deleteEmployee(id);
            setSnackbar({ open: true, message: 'Employee deleted.', severity: 'success' });
            loadEmployees();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Error deleting employee.', severity: 'error' });
        }
    };

    const handleSaveCharge = async (e) => {
        e.preventDefault();
        try {
            await apiService.addEmployeeCharge(chargeDialog.employeeId, chargeForm);
            setSnackbar({ open: true, message: 'Charge recorded.', severity: 'success' });
            setChargeDialog({ open: false, employeeId: null });
            setChargeForm(emptyChargeForm);
            loadEmployees();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Error recording charge.', severity: 'error' });
        }
    };

    const handleRecordPayment = async (e) => {
        e.preventDefault();
        try {
            await apiService.recordEmployeePayment(paymentDialog.employeeId, paymentForm);
            setSnackbar({ open: true, message: 'Payment recorded successfully.', severity: 'success' });
            setPaymentDialog({ open: false, employeeId: null });
            setPaymentForm(emptyPaymentForm);
            loadEmployees();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Error recording payment.', severity: 'error' });
        }
    };

    const handleOpenHistory = async (employee) => {
        setHistoryDialog({ open: true, employee, history: [] });
        setFixBalanceInput(employee.balance);
        setHistoryLoading(true);
        try {
            const res = await apiService.fetchEmployeeHistory(employee.id);
            setHistoryDialog({ open: true, employee: res.data.employee, history: res.data.history });
            setFixBalanceInput(res.data.employee.balance);
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to load employee history.', severity: 'error' });
        } finally {
            setHistoryLoading(false);
        }
    };

    const handleFixBalance = async () => {
        if (!historyDialog.employee) return;
        try {
            const res = await apiService.fixEmployeeBalance(historyDialog.employee.id, { balance: fixBalanceInput });
            setSnackbar({ open: true, message: 'Balance updated successfully.', severity: 'success' });
            setHistoryDialog({ ...historyDialog, employee: res.data.employee });
            loadEmployees();
        } catch (err) {
            setSnackbar({ open: true, message: 'Error updating balance.', severity: 'error' });
        }
    };

    if (loading) return <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}><CircularProgress /></Box>;

    return (
        <Box sx={{ maxWidth: 1100, mx: 'auto', p: 2 }}>
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                <Typography variant="h5" sx={{ fontWeight: 600 }}>Payroll</Typography>
                <Button variant="contained" startIcon={<AddIcon />} onClick={() => openEmployeeDialog(null)} sx={{ width: { xs: '100%', sm: 'auto' } }}>
                    Add Employee
                </Button>
            </Box>

            <TableContainer component={Paper} elevation={2} sx={{ borderRadius: 2 }}>
                <Table>
                    <TableHead sx={{ bgcolor: alpha(theme.palette.primary.main, 0.05) }}>
                        <TableRow>
                            <TableCell><b>Name</b></TableCell>
                            <TableCell><b>Monthly Salary</b></TableCell>
                            <TableCell><b>Balance Owed</b></TableCell>
                            <TableCell><b>Status</b></TableCell>
                            <TableCell align="right"><b>Actions</b></TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {employees.map((e) => (
                            <TableRow key={e.id}>
                                <TableCell>{e.name}</TableCell>
                                <TableCell>${e.monthly_salary.toFixed(2)}</TableCell>
                                <TableCell>
                                    <Chip
                                        icon={<BalanceIcon />}
                                        label={`$${e.balance.toFixed(2)}`}
                                        color={e.balance > 0 ? 'error' : 'success'}
                                        variant="outlined"
                                    />
                                </TableCell>
                                <TableCell>
                                    <Chip size="small" label={e.active ? 'Active' : 'Inactive'} color={e.active ? 'success' : 'default'} />
                                </TableCell>
                                <TableCell align="right">
                                    <Tooltip title="Action History">
                                        <IconButton color="info" onClick={() => handleOpenHistory(e)}>
                                            <HistoryIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Add Bonus / Deduction">
                                        <IconButton color="warning" onClick={() => { setChargeForm(emptyChargeForm); setChargeDialog({ open: true, employeeId: e.id }); }}>
                                            <BonusIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Pay Salary / Advance">
                                        <IconButton color="success" onClick={() => { setPaymentForm(emptyPaymentForm); setPaymentDialog({ open: true, employeeId: e.id }); }}>
                                            <PaymentIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Edit">
                                        <IconButton color="primary" onClick={() => openEmployeeDialog(e)}>
                                            <EditIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Delete">
                                        <IconButton color="error" onClick={() => handleDeleteEmployee(e.id)}>
                                            <DeleteIcon />
                                        </IconButton>
                                    </Tooltip>
                                </TableCell>
                            </TableRow>
                        ))}
                        {employees.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={5} align="center" sx={{ py: 3, color: 'text.secondary' }}>No employees found.</TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </TableContainer>

            {/* Employee Form Dialog */}
            <Dialog open={employeeDialog.open} onClose={() => setEmployeeDialog({ open: false, data: null })} maxWidth="sm" fullWidth>
                <form onSubmit={handleSaveEmployee}>
                    <DialogTitle>{employeeDialog.data ? 'Edit Employee' : 'Add Employee'}</DialogTitle>
                    <DialogContent dividers>
                        <TextField fullWidth margin="dense" label="Name" value={employeeForm.name}
                            onChange={(ev) => setEmployeeForm({ ...employeeForm, name: ev.target.value })} required />
                        <TextField fullWidth margin="dense" label="Monthly Salary ($)" type="number" inputProps={{ step: "0.01" }}
                            value={employeeForm.monthly_salary}
                            onChange={(ev) => setEmployeeForm({ ...employeeForm, monthly_salary: ev.target.value })} required />
                        <TextField fullWidth margin="dense" label="Hire Date" type="date" InputLabelProps={{ shrink: true }}
                            value={employeeForm.hire_date}
                            onChange={(ev) => setEmployeeForm({ ...employeeForm, hire_date: ev.target.value })} />
                        <TextField fullWidth margin="dense" label="Notes" multiline rows={3}
                            value={employeeForm.notes}
                            onChange={(ev) => setEmployeeForm({ ...employeeForm, notes: ev.target.value })} />
                        <FormControlLabel
                            control={<Switch checked={!!employeeForm.active}
                                onChange={(ev) => setEmployeeForm({ ...employeeForm, active: ev.target.checked })} />}
                            label="Active"
                            sx={{ mt: 1 }}
                        />
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={() => setEmployeeDialog({ open: false, data: null })}>Cancel</Button>
                        <Button type="submit" variant="contained">Save</Button>
                    </DialogActions>
                </form>
            </Dialog>

            {/* Bonus / Deduction Dialog */}
            <Dialog open={chargeDialog.open} onClose={() => setChargeDialog({ open: false, employeeId: null })} maxWidth="xs" fullWidth>
                <form onSubmit={handleSaveCharge}>
                    <DialogTitle>Add Bonus / Deduction</DialogTitle>
                    <DialogContent dividers>
                        <FormControl fullWidth margin="dense" size="small">
                            <InputLabel>Type</InputLabel>
                            <Select label="Type" value={chargeForm.type}
                                onChange={(ev) => setChargeForm({ ...chargeForm, type: ev.target.value })}>
                                <MenuItem value="bonus">Bonus</MenuItem>
                                <MenuItem value="deduction">Deduction</MenuItem>
                                <MenuItem value="salary">Manual Salary Charge</MenuItem>
                            </Select>
                        </FormControl>
                        <TextField
                            fullWidth autoFocus margin="dense" label="Amount" type="number"
                            value={chargeForm.amount} onChange={(ev) => setChargeForm({ ...chargeForm, amount: ev.target.value })}
                            inputProps={{ step: "0.01", min: "0.01" }} required
                        />
                        <TextField
                            fullWidth margin="dense" label="Reason" value={chargeForm.reason}
                            onChange={(ev) => setChargeForm({ ...chargeForm, reason: ev.target.value })}
                        />
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={() => setChargeDialog({ open: false, employeeId: null })}>Cancel</Button>
                        <Button type="submit" variant="contained" color="warning">Save</Button>
                    </DialogActions>
                </form>
            </Dialog>

            {/* Pay Salary / Advance Dialog */}
            <Dialog open={paymentDialog.open} onClose={() => setPaymentDialog({ open: false, employeeId: null })} maxWidth="xs" fullWidth>
                <form onSubmit={handleRecordPayment}>
                    <DialogTitle>Pay Salary / Advance</DialogTitle>
                    <DialogContent dividers>
                        <Typography variant="body2" sx={{ mb: 2 }}>
                            Enter the amount you paid this employee. This reduces the balance owed (an advance can push it negative).
                        </Typography>
                        <TextField
                            fullWidth autoFocus margin="dense" label="Amount Paid" type="number"
                            value={paymentForm.amount} onChange={(ev) => setPaymentForm({ ...paymentForm, amount: ev.target.value })}
                            inputProps={{ step: "0.01", min: "0.01" }} required
                        />
                        <TextField
                            fullWidth margin="dense" label="Method" value={paymentForm.method}
                            onChange={(ev) => setPaymentForm({ ...paymentForm, method: ev.target.value })}
                        />
                        <TextField
                            fullWidth margin="dense" label="Note" value={paymentForm.note}
                            onChange={(ev) => setPaymentForm({ ...paymentForm, note: ev.target.value })}
                        />
                        <FormControlLabel
                            control={<Switch checked={paymentForm.is_advance}
                                onChange={(ev) => setPaymentForm({ ...paymentForm, is_advance: ev.target.checked })} />}
                            label="This is an advance"
                            sx={{ mt: 1 }}
                        />
                    </DialogContent>
                    <DialogActions>
                        <Button onClick={() => setPaymentDialog({ open: false, employeeId: null })}>Cancel</Button>
                        <Button type="submit" variant="contained" color="success">Record Payment</Button>
                    </DialogActions>
                </form>
            </Dialog>

            {/* Action History & Fixed Balance Dialog */}
            <Dialog open={historyDialog.open} onClose={() => setHistoryDialog({ open: false, employee: null, history: [] })} maxWidth="md" fullWidth>
                <DialogTitle>Employee Action History & Balance Management</DialogTitle>
                <DialogContent dividers>
                    {historyDialog.employee && (
                        <Box sx={{ mb: 3, p: 2, bgcolor: alpha(theme.palette.primary.main, 0.05), borderRadius: 2 }}>
                            <Typography variant="subtitle1" fontWeight={600}>{historyDialog.employee.name}</Typography>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 1, flexWrap: 'wrap' }}>
                                <Typography variant="body2">Fixed Balance:</Typography>
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
                                    Set Balance
                                </Button>
                            </Box>
                        </Box>
                    )}

                    {historyLoading ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>
                    ) : (
                        <TableContainer>
                            <Table size="small">
                                <TableHead>
                                    <TableRow>
                                        <TableCell><b>Date</b></TableCell>
                                        <TableCell><b>Type</b></TableCell>
                                        <TableCell><b>Description</b></TableCell>
                                        <TableCell align="right"><b>Amount</b></TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {historyDialog.history.map((h) => (
                                        <TableRow key={h.id}>
                                            <TableCell>{h.date}</TableCell>
                                            <TableCell>
                                                <Chip size="small" label={h.title} />
                                            </TableCell>
                                            <TableCell>{h.description}</TableCell>
                                            <TableCell align="right" sx={{ fontWeight: 600, color: h.amount > 0 ? 'error.main' : 'success.main' }}>
                                                {h.amount > 0 ? `+$${h.amount.toFixed(2)}` : `-$${Math.abs(h.amount).toFixed(2)}`}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                    {historyDialog.history.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={4} align="center" sx={{ py: 3, color: 'text.secondary' }}>No action history found.</TableCell>
                                        </TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setHistoryDialog({ open: false, employee: null, history: [] })}>Close</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
}

export default EmployeesView;
