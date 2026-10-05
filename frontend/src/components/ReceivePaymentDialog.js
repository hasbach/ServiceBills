import React, { useEffect, useState } from 'react';
import {
    Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, MenuItem,
    Typography, Alert, CircularProgress
} from '@mui/material';
import { apiService } from '../context/AppContext.js';

/**
 * Take one amount from a customer and apply it to their bills, oldest first
 * (POST /api/customers/<id>/receive-payment). mode 'pay' confirms it now
 * (admin/finance; any extra becomes credit); 'collect' records it as
 * collected, waiting for finance to confirm (capped at what's owed).
 */
function ReceivePaymentDialog({ open, onClose, onDone, customer, owed, mode = 'pay' }) {
    const [amount, setAmount] = useState('');
    const [method, setMethod] = useState('cash');
    const [reference, setReference] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
        if (open) {
            setAmount(owed > 0 ? owed.toFixed(2) : '');
            setMethod('cash');
            setReference('');
            setError(null);
        }
    }, [open, owed]);

    const value = parseFloat(amount);
    const valid = !isNaN(value) && value > 0;
    const extra = valid ? value - owed : 0;

    let hint = `Pays the oldest unpaid bills first. Owed: $${owed.toFixed(2)}.`;
    if (valid && extra < -0.004) hint = `Partial: $${(-extra).toFixed(2)} will still be owed.`;
    else if (valid && extra > 0.004) {
        hint = mode === 'pay'
            ? `All bills paid; $${extra.toFixed(2)} kept as credit for future bills.`
            : `That's more than owed — collect up to $${owed.toFixed(2)}.`;
    } else if (valid) hint = 'Pays everything owed.';

    const submit = async () => {
        if (!valid || submitting) return;
        setSubmitting(true);
        setError(null);
        try {
            const res = await apiService.receiveCustomerPayment(customer.id, {
                amount: value,
                action: mode,
                method,
                ...(method === 'whish_transfer' && reference.trim() ? { reference: reference.trim() } : {}),
            });
            onDone(res.data, value);
        } catch (err) {
            setError(err.response?.data?.message || err.response?.data?.error || 'Could not record the payment.');
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onClose={() => !submitting && onClose()} maxWidth="xs" fullWidth>
            <DialogTitle sx={{ fontWeight: 700 }}>{mode === 'pay' ? 'Receive Payment' : 'Collect Payment'}</DialogTitle>
            <DialogContent>
                <Typography variant="body2" sx={{ mb: 2, color: 'text.secondary' }}>
                    Customer: <strong>{customer?.name}</strong>
                </Typography>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
                <TextField fullWidth autoFocus type="number" label={mode === 'pay' ? 'Amount received' : 'Amount collected'}
                    value={amount} onChange={(e) => setAmount(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') submit(); }}
                    InputProps={{ inputProps: { min: 0.01, step: 0.01 } }}
                    helperText={hint}
                    error={mode === 'collect' && extra > 0.004} />
                <TextField select fullWidth sx={{ mt: 2 }} label="Paid by" value={method} onChange={(e) => setMethod(e.target.value)}>
                    <MenuItem value="cash">Cash</MenuItem>
                    <MenuItem value="whish_transfer">Whish transfer (sent directly to our Whish account)</MenuItem>
                </TextField>
                {method === 'whish_transfer' && (
                    <TextField fullWidth sx={{ mt: 2 }} label="Whish reference (optional)" value={reference}
                        inputProps={{ maxLength: 64 }} onChange={(e) => setReference(e.target.value)}
                        helperText="Not counted as cash on the Daily Cash report" />
                )}
                {mode === 'collect' && (
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
                        Finance confirms it later with one click (“Confirm collected”).
                    </Typography>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={submitting}>Cancel</Button>
                <Button variant="contained" color="success" onClick={submit}
                    disabled={!valid || submitting || (mode === 'collect' && extra > 0.004)}
                    startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : null}>
                    {mode === 'pay' ? 'Receive' : 'Collect'}
                </Button>
            </DialogActions>
        </Dialog>
    );
}

export default ReceivePaymentDialog;
