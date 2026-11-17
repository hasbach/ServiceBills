import React, { useState } from 'react';
import {
    Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, MenuItem, TextField, Tooltip
} from '@mui/material';
import { Edit as EditIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { askDeleteReason } from '../utils/deleteReason.js';

/**
 * Edit / delete buttons for one recorded reseller or upstream entry (admin
 * only; the server enforces it too). The party's balance moves by the
 * difference, and the change is kept in Finance > History.
 *  - onUpdate(payload) and onDelete(reason) return promises; errors are the caller's to show.
 *  - showChannel: the entry moved cash or Whish, so the channel can be corrected.
 */
function EntryActions({ row, showChannel = false, onUpdate, onDelete }) {
    const [open, setOpen] = useState(false);
    const [amount, setAmount] = useState('');
    const [description, setDescription] = useState('');
    const [paidVia, setPaidVia] = useState('cash');
    const [saving, setSaving] = useState(false);

    const start = () => {
        setAmount(String(row.amount));
        setDescription(row.description || '');
        setPaidVia(row.paid_via || 'cash');
        setOpen(true);
    };

    const save = async () => {
        const payload = { amount: parseFloat(amount), description };
        if (showChannel) payload.paid_via = paidVia;
        setSaving(true);
        try {
            await onUpdate(payload);
            setOpen(false);
        } catch (e) {
            // the caller already showed the error; keep the dialog open
        } finally {
            setSaving(false);
        }
    };

    const remove = () => {
        const reason = askDeleteReason(`Delete this entry of $${parseFloat(row.amount).toFixed(2)}? The balance is adjusted to match.`);
        if (reason) onDelete(reason);
    };

    const valid = parseFloat(amount) > 0;

    return (
        <Box sx={{ whiteSpace: 'nowrap' }}>
            <Tooltip title="Edit entry"><IconButton size="small" onClick={start}><EditIcon fontSize="small" /></IconButton></Tooltip>
            <Tooltip title="Delete entry"><IconButton size="small" color="error" onClick={remove}><DeleteIcon fontSize="small" /></IconButton></Tooltip>
            <Dialog open={open} onClose={() => setOpen(false)} fullWidth maxWidth="xs">
                <DialogTitle>Edit entry</DialogTitle>
                <DialogContent dividers>
                    <TextField fullWidth margin="dense" label="Amount ($)" type="number" inputProps={{ step: '0.01', min: 0 }}
                        value={amount} onChange={(e) => setAmount(e.target.value)} autoFocus
                        helperText="The balance moves by the difference." />
                    <TextField fullWidth margin="dense" label="Description" value={description}
                        onChange={(e) => setDescription(e.target.value)} inputProps={{ maxLength: 200 }} />
                    {showChannel && (
                        <TextField fullWidth select margin="dense" label="Paid via" value={paidVia} onChange={(e) => setPaidVia(e.target.value)}>
                            <MenuItem value="cash">Cash</MenuItem>
                            <MenuItem value="whish">Whish</MenuItem>
                        </TextField>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={save} disabled={!valid || saving}>Save</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
}

export default EntryActions;
