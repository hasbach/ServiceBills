import React, { useEffect, useState } from 'react';
import {
    Dialog, DialogTitle, DialogContent, DialogActions, Button, Typography, Alert, CircularProgress,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Box
} from '@mui/material';
import { apiService } from '../context/AppContext.js';

/**
 * Lost-credit review (admin/finance). Before the 2026-10 fix, settling bills
 * from a customer's credit also took the bill's amount off their balance a
 * second time. This lists the customers it happened to; Restore gives the
 * amount back, Dismiss marks it reviewed (e.g. already fixed by hand).
 */
function CreditReviewDialog({ open, onClose, onChanged }) {
    const [rows, setRows] = useState(null);
    const [busy, setBusy] = useState(null); // customer id being resolved
    const [error, setError] = useState(null);

    const load = async () => {
        setError(null);
        try {
            const res = await apiService.getCreditReview();
            setRows(res.data.customers);
        } catch (err) {
            setError('Could not load the review.');
            setRows([]);
        }
    };

    useEffect(() => {
        if (open) { setRows(null); load(); }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    const resolve = async (customerId, action) => {
        setBusy(customerId);
        setError(null);
        try {
            await apiService.resolveCreditReview(customerId, action);
            await load();
            onChanged();
        } catch (err) {
            setError(err.response?.data?.message || 'Could not save.');
        } finally {
            setBusy(null);
        }
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
            <DialogTitle sx={{ fontWeight: 700 }}>Credit check</DialogTitle>
            <DialogContent dividers>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Until this update, when a bill was paid automatically from a customer's existing credit, the
                    bill's amount was also taken off their balance a second time. These customers were affected.
                    <strong> Restore</strong> adds the amount back to their balance; <strong>Dismiss</strong> leaves
                    the balance as it is (for example if you already corrected it by hand).
                </Typography>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
                {rows === null ? (
                    <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}><CircularProgress /></Box>
                ) : rows.length === 0 ? (
                    <Alert severity="success">Nothing to review — no customer is affected.</Alert>
                ) : (
                    <TableContainer>
                        <Table size="small">
                            <TableHead>
                                <TableRow>
                                    <TableCell><b>Customer</b></TableCell>
                                    <TableCell><b>Bills paid from credit</b></TableCell>
                                    <TableCell align="right"><b>Balance now</b></TableCell>
                                    <TableCell align="right"><b>To restore</b></TableCell>
                                    <TableCell align="right" />
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {rows.map((c) => (
                                    <TableRow key={c.customer_id}>
                                        <TableCell sx={{ fontWeight: 600 }}>{c.customer_name}</TableCell>
                                        <TableCell sx={{ color: 'text.secondary' }}>
                                            {c.bills.map((b) => `$${b.amount.toFixed(2)} (${b.date})`).join(', ')}
                                        </TableCell>
                                        <TableCell align="right">${c.balance.toFixed(2)}</TableCell>
                                        <TableCell align="right" sx={{ fontWeight: 700, color: 'success.main' }}>
                                            +${c.suggested_credit.toFixed(2)}
                                        </TableCell>
                                        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                                            {busy === c.customer_id ? <CircularProgress size={18} /> : (
                                                <>
                                                    <Button size="small" variant="contained" color="success"
                                                        onClick={() => resolve(c.customer_id, 'restore')} sx={{ mr: 1 }}>Restore</Button>
                                                    <Button size="small" onClick={() => resolve(c.customer_id, 'dismiss')}>Dismiss</Button>
                                                </>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </TableContainer>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose}>Close</Button>
            </DialogActions>
        </Dialog>
    );
}

export default CreditReviewDialog;
