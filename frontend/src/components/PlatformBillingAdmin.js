import React, { useEffect, useMemo, useState } from 'react';
import {
    Box, Typography, Table, TableHead, TableRow, TableCell, TableBody, Chip, Paper,
    CircularProgress, Stack, TextField, ToggleButtonGroup, ToggleButton, Card, CardContent, Button,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

// Every Pro-plan payment across tenants: Whish checkouts (any status) and
// payments recorded by hand on a Grant/Extend Pro (bank transfer, cash...).

export const METHOD_LABELS = {
    whish: 'Whish (online)',
    transfer: 'Bank transfer',
    cash: 'Cash',
    whish_direct: 'Whish (direct)',
    other: 'Other',
};

const PERIOD_LABELS = {
    monthly: '1 month', yearly: '1 year',
    '1_month': '1 month', '1_year': '1 year', indefinite: 'Indefinite', custom: 'Custom date',
};

const STATUS_COLORS = { succeeded: 'success', failed: 'error', pending: 'warning', expired: 'default' };
const STATUS_LABELS = { succeeded: 'Paid', failed: 'Failed', pending: 'Pending', expired: 'Expired' };

const money = (n) => `$${Number(n || 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const when = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
};

const Stat = ({ label, value }) => (
    <Card variant="outlined" sx={{ flex: '1 1 160px' }}>
        <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
            <Typography variant="caption" color="text.secondary">{label}</Typography>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>{value}</Typography>
        </CardContent>
    </Card>
);

const PlatformBillingAdmin = () => {
    const { apiService } = useAppContext();
    const [rows, setRows] = useState(null);
    const [error, setError] = useState(false);
    const [status, setStatus] = useState('all');
    const [query, setQuery] = useState('');

    const load = () => {
        setRows(null);
        setError(false);
        apiService.adminBillingPayments()
            .then((r) => setRows(r.data))
            .catch(() => { setRows([]); setError(true); });
    };
    useEffect(load, [apiService]); // eslint-disable-line react-hooks/exhaustive-deps

    const totals = useMemo(() => {
        const paid = (rows || []).filter((r) => r.status === 'succeeded');
        const now = new Date();
        const thisMonth = paid.filter((r) => {
            const d = new Date(r.completed_at || r.created_at);
            return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
        });
        const sum = (list) => list.reduce((s, r) => s + Number(r.amount || 0), 0);
        return {
            all: sum(paid),
            month: sum(thisMonth),
            whish: sum(paid.filter((r) => r.source === 'whish')),
            manual: sum(paid.filter((r) => r.source === 'manual')),
        };
    }, [rows]);

    const visible = useMemo(() => {
        const q = query.trim().toLowerCase();
        return (rows || []).filter((r) => (status === 'all' || r.status === status)
            && (!q || (r.tenant_name || '').toLowerCase().includes(q)));
    }, [rows, status, query]);

    if (!rows) return <CircularProgress />;

    return (
        <Box>
            <Stack direction="row" spacing={2} useFlexGap flexWrap="wrap" sx={{ mb: 3 }}>
                <Stat label="Received (all time)" value={money(totals.all)} />
                <Stat label="Received this month" value={money(totals.month)} />
                <Stat label="Via Whish online" value={money(totals.whish)} />
                <Stat label="Recorded by hand" value={money(totals.manual)} />
            </Stack>

            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 2 }} alignItems={{ sm: 'center' }}>
                <ToggleButtonGroup size="small" exclusive value={status} onChange={(e, v) => v && setStatus(v)}>
                    <ToggleButton value="all">All</ToggleButton>
                    <ToggleButton value="succeeded">Paid</ToggleButton>
                    <ToggleButton value="pending">Pending</ToggleButton>
                    <ToggleButton value="failed">Failed</ToggleButton>
                </ToggleButtonGroup>
                <TextField size="small" label="Search tenant" value={query} onChange={(e) => setQuery(e.target.value)} />
                <Box sx={{ flexGrow: 1 }} />
                <Button size="small" onClick={load}>Refresh</Button>
            </Stack>

            {error && <Typography color="error" sx={{ mb: 2 }}>Could not load payments.</Typography>}

            <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
                <Table size="small">
                    <TableHead>
                        <TableRow>
                            <TableCell>Date</TableCell>
                            <TableCell>Tenant</TableCell>
                            <TableCell align="right">Amount</TableCell>
                            <TableCell>Method</TableCell>
                            <TableCell>Period</TableCell>
                            <TableCell>Status</TableCell>
                            <TableCell>Note</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {visible.length === 0 && (
                            <TableRow>
                                <TableCell colSpan={7} sx={{ color: 'text.secondary', textAlign: 'center', py: 3 }}>
                                    No payments yet. Whish payments appear here automatically; record a bank
                                    transfer or cash payment from Tenants → Grant/Extend Pro.
                                </TableCell>
                            </TableRow>
                        )}
                        {visible.map((r) => (
                            <TableRow key={r.id} hover>
                                <TableCell sx={{ whiteSpace: 'nowrap' }}>{when(r.completed_at || r.created_at)}</TableCell>
                                <TableCell>{r.tenant_name || `#${r.tenant_id}`}</TableCell>
                                <TableCell align="right">{money(r.amount)} {r.currency !== 'USD' ? r.currency : ''}</TableCell>
                                <TableCell>{METHOD_LABELS[r.method] || r.method}</TableCell>
                                <TableCell>{PERIOD_LABELS[r.period] || r.period || '—'}</TableCell>
                                <TableCell>
                                    <Chip size="small" color={STATUS_COLORS[r.status] || 'default'}
                                          label={STATUS_LABELS[r.status] || r.status} />
                                </TableCell>
                                <TableCell sx={{ color: 'text.secondary' }}>
                                    {r.note || ''}
                                    {r.recorded_by && (
                                        <Typography variant="caption" display="block">by {r.recorded_by}</Typography>
                                    )}
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </Paper>
        </Box>
    );
};

export default PlatformBillingAdmin;
