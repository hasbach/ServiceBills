import React from 'react';
import { TextField, MenuItem } from '@mui/material';

/**
 * "Paid by" for money going in or out outside customer bills (expenses,
 * supplier payments, upstream top-ups, reseller collections): 'cash' or
 * 'whish'. Drives the Cash vs Whish columns of the Daily Cash flow.
 */
function PaidViaSelect({ value, onChange, ...props }) {
    return (
        <TextField select fullWidth size="small" margin="normal" label="Paid by"
            value={value || 'cash'} onChange={(e) => onChange(e.target.value)} {...props}>
            <MenuItem value="cash">Cash</MenuItem>
            <MenuItem value="whish">Whish</MenuItem>
        </TextField>
    );
}

export default PaidViaSelect;
