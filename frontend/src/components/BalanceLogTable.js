import React, { useState, useEffect } from 'react';
import {
    Box, Typography, CircularProgress, Table, TableBody, TableCell,
    TableContainer, TableHead, TableRow
} from '@mui/material';

const money = (v) => `${v < 0 ? '-' : ''}$${Math.abs(v).toFixed(2)}`;

/**
 * Every change of a reseller's / supplier's balance: what it was, what it
 * became, and why. `load` is a () => Promise<axios response> (e.g.
 * () => apiService.getSupplierBalanceLog(id)); re-mount with a new `key` to
 * refetch after an action changes the balance.
 */
function BalanceLogTable({ load }) {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        load()
            .then(res => { if (alive) setRows(res.data || []); })
            .catch(() => { if (alive) setError(true); })
            .finally(() => { if (alive) setLoading(false); });
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    if (loading) return <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>;
    if (error) return <Typography color="error" sx={{ p: 2 }}>Failed to load the balance log.</Typography>;
    if (rows.length === 0) {
        return (
            <Typography sx={{ textAlign: 'center', color: 'text.secondary', p: 4 }}>
                No balance changes logged yet. Changes are recorded from now on.
            </Typography>
        );
    }

    return (
        <TableContainer>
            <Table size="small">
                <TableHead>
                    <TableRow>
                        <TableCell><b>Date</b></TableCell>
                        <TableCell><b>What happened</b></TableCell>
                        <TableCell align="right"><b>Balance before</b></TableCell>
                        <TableCell align="right"><b>Change</b></TableCell>
                        <TableCell align="right"><b>Balance after</b></TableCell>
                        <TableCell><b>By</b></TableCell>
                    </TableRow>
                </TableHead>
                <TableBody>
                    {rows.map(r => (
                        <TableRow key={r.id}>
                            <TableCell sx={{ whiteSpace: 'nowrap' }}>{new Date(r.date).toLocaleString()}</TableCell>
                            <TableCell>{r.reason}</TableCell>
                            <TableCell align="right">{money(r.balance_before)}</TableCell>
                            <TableCell align="right" sx={{ fontWeight: 600, whiteSpace: 'nowrap', color: r.change > 0 ? 'error.main' : 'success.main' }}>
                                {r.change > 0 ? '+' : ''}{money(r.change)}
                            </TableCell>
                            <TableCell align="right" sx={{ fontWeight: 700 }}>{money(r.balance_after)}</TableCell>
                            <TableCell sx={{ color: 'text.secondary' }}>{r.changed_by || 'System'}</TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
        </TableContainer>
    );
}

export default BalanceLogTable;
