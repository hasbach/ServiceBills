import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, CircularProgress, Table, TableBody, TableCell, TableContainer,
    TableHead, TableRow, TextField, MenuItem, Chip, Button, Alert
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const ENTITY_LABELS = {
    payment: 'Customer payment / bill',
    expense: 'Expense',
    supplier_payment: 'Supplier payment',
    salary_charge: 'Salary charge',
    salary_payment: 'Salary payment',
    cash_entry: 'Cash entry',
    account_transfer: 'Transfer',
    reseller_payment: 'Reseller entry',
    upstream_payment: 'Upstream entry',
    day_close: 'Day close',
};

const FIELD_LABELS = {
    amount: 'Amount', paid: 'Paid', paid_at: 'Paid at', collected: 'Collected', collected_at: 'Collected at',
    collected_amount: 'Collected amount', collected_via: 'Channel', paid_via: 'Channel', date: 'Date',
    reason: 'Reason', description: 'Description', note: 'Note', is_gratis: 'Gratis', gratis_note: 'Gratis note',
    reverted_at: 'Reverted at', revert_reason: 'Revert reason', is_refund: 'Refund', refund_reason: 'Refund reason',
    refunded_at: 'Refunded at', settled_from_credit: 'Paid from credit', pre_payment: 'Prepayment',
    type: 'Type', period: 'Period', account: 'Account', from_account: 'From', to_account: 'To',
    payment_date: 'Payment date', payment_method: 'Method', method: 'Method', reference_note: 'Reference',
    category_id: 'Category', is_advance: 'Advance', is_credit: 'On credit', day: 'Day',
    expected_cash: 'Expected cash', counted_cash: 'Counted cash', expected_whish: 'Expected Whish',
    counted_whish: 'Counted Whish', whish_transaction_number: 'Whish number', credit_review: 'Credit review',
};
// Shown first, and the only ones shown for a created or deleted record.
const KEY_FIELDS = ['amount', 'date', 'day', 'payment_date', 'type', 'period', 'account', 'from_account', 'to_account',
    'reason', 'description', 'note', 'reference_note', 'paid', 'paid_via', 'collected_via',
    'counted_cash', 'counted_whish'];
const HIDDEN_FIELDS = new Set(['currency', 'fx_rate_to_reporting', 'customer_id', 'supplier_id', 'employee_id',
    'reseller_id', 'upstream_provider_id', 'created_by_id', 'closed_by_id', 'collected_by_id', 'received_by_id',
    'reverted_by_id', 'refunded_by_id', 'created_at', 'closed_at']);
const MONEY_FIELDS = new Set(['amount', 'collected_amount', 'expected_cash', 'counted_cash', 'expected_whish', 'counted_whish']);

const money = (v) => `${v < 0 ? '-' : ''}$${Math.abs(Number(v)).toFixed(2)}`;

function show(field, v) {
    if (v === null || v === undefined || v === '') return '—';
    if (v === true) return 'Yes';
    if (v === false) return 'No';
    if (MONEY_FIELDS.has(field) && !Number.isNaN(Number(v))) return money(v);
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T00:00:00$/.test(v)) return v.slice(0, 10);
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(v)) return new Date(`${v}Z`).toLocaleString();
    return String(v);
}

function actionChip(row) {
    if (row.entity === 'day_close') {
        return row.action === 'delete'
            ? <Chip size="small" color="warning" label="Reopened" />
            : <Chip size="small" color="success" label="Closed" />;
    }
    if (row.action === 'create') return <Chip size="small" color="success" variant="outlined" label="Created" />;
    if (row.action === 'delete') return <Chip size="small" color="error" label="Deleted" />;
    return <Chip size="small" color="info" variant="outlined" label="Edited" />;
}

function Changes({ row }) {
    const fields = Object.keys(row.changes || {}).filter((f) => !HIDDEN_FIELDS.has(f));
    if (row.action === 'update') {
        const ordered = [...fields].sort((a, b) => (KEY_FIELDS.indexOf(a) + 100) % 100 - (KEY_FIELDS.indexOf(b) + 100) % 100);
        return (
            <Box>
                {ordered.map((f) => (
                    <Typography key={f} variant="body2">
                        <b>{FIELD_LABELS[f] || f}:</b>{' '}
                        <Box component="span" sx={{ color: 'text.secondary', textDecoration: 'line-through' }}>{show(f, row.changes[f][0])}</Box>
                        {' → '}{show(f, row.changes[f][1])}
                    </Typography>
                ))}
            </Box>
        );
    }
    const shown = KEY_FIELDS.filter((f) => fields.includes(f) && row.changes[f] !== null && row.changes[f] !== '' && row.changes[f] !== false);
    return (
        <Typography variant="body2" sx={row.action === 'delete' ? { textDecoration: 'line-through', color: 'text.secondary' } : undefined}>
            {shown.map((f) => `${FIELD_LABELS[f] || f}: ${show(f, row.changes[f])}`).join(' · ')}
        </Typography>
    );
}

const PER_PAGE = 50;

/** History of every financial create / edit / delete: who, when, what changed. */
function AuditLogView() {
    const { apiService } = useAppContext();
    const [filters, setFilters] = useState({ entity: '', action: '', user: '', start_date: '', end_date: '' });
    const [items, setItems] = useState([]);
    const [users, setUsers] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    const load = useCallback((pageToLoad, activeFilters) => {
        setLoading(true);
        setError(false);
        const params = { page: pageToLoad, per_page: PER_PAGE };
        Object.entries(activeFilters).forEach(([k, v]) => { if (v) params[k] = v; });
        apiService.getAuditLog(params)
            .then((res) => {
                setItems((prev) => (pageToLoad === 1 ? res.data.items : [...prev, ...res.data.items]));
                setTotal(res.data.total);
                setUsers(res.data.users || []);
                setPage(pageToLoad);
            })
            .catch(() => setError(true))
            .finally(() => setLoading(false));
    }, [apiService]);

    useEffect(() => { load(1, filters); }, [load, filters]);

    const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }));

    return (
        <Box>
            <Typography variant="h6" sx={{ mb: 0.5 }}>History</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Every financial record that was created, edited or deleted: who did it, when, and what changed.
                Recording started when this screen was added; earlier changes are not listed.
            </Typography>
            <Paper sx={{ p: 2, mb: 2, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                <TextField select size="small" label="Record type" value={filters.entity} onChange={set('entity')} sx={{ minWidth: 200 }}>
                    <MenuItem value="">All</MenuItem>
                    {Object.entries(ENTITY_LABELS).map(([k, label]) => <MenuItem key={k} value={k}>{label}</MenuItem>)}
                </TextField>
                <TextField select size="small" label="Action" value={filters.action} onChange={set('action')} sx={{ minWidth: 140 }}>
                    <MenuItem value="">All</MenuItem>
                    <MenuItem value="create">Created</MenuItem>
                    <MenuItem value="update">Edited</MenuItem>
                    <MenuItem value="delete">Deleted</MenuItem>
                </TextField>
                <TextField select size="small" label="By" value={filters.user} onChange={set('user')} sx={{ minWidth: 160 }}>
                    <MenuItem value="">Everyone</MenuItem>
                    <MenuItem value="system">System (automatic)</MenuItem>
                    {users.map((u) => <MenuItem key={u} value={u}>{u}</MenuItem>)}
                </TextField>
                <TextField size="small" type="date" label="From" value={filters.start_date} onChange={set('start_date')} InputLabelProps={{ shrink: true }} />
                <TextField size="small" type="date" label="To" value={filters.end_date} onChange={set('end_date')} InputLabelProps={{ shrink: true }} />
            </Paper>

            {error && <Alert severity="error" sx={{ mb: 2 }}>Failed to load the history.</Alert>}
            <Paper>
                <TableContainer>
                    <Table size="small">
                        <TableHead>
                            <TableRow>
                                <TableCell><b>When</b></TableCell>
                                <TableCell><b>By</b></TableCell>
                                <TableCell><b>Action</b></TableCell>
                                <TableCell><b>Record</b></TableCell>
                                <TableCell><b>What changed</b></TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {items.map((r) => (
                                <TableRow key={r.id} sx={{ verticalAlign: 'top' }}>
                                    <TableCell sx={{ whiteSpace: 'nowrap' }}>{new Date(r.date).toLocaleString()}</TableCell>
                                    <TableCell sx={{ color: r.changed_by ? 'text.primary' : 'text.secondary' }}>{r.changed_by || 'System'}</TableCell>
                                    <TableCell>{actionChip(r)}</TableCell>
                                    <TableCell>
                                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                                            {ENTITY_LABELS[r.entity] || r.entity}{r.entity_id != null ? ` #${r.entity_id}` : ''}
                                        </Typography>
                                        <Typography variant="body2" color="text.secondary">
                                            {[r.party_name, r.amount != null ? money(r.amount) : null].filter(Boolean).join(' · ')}
                                        </Typography>
                                    </TableCell>
                                    <TableCell><Changes row={r} /></TableCell>
                                </TableRow>
                            ))}
                            {!loading && items.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={5} sx={{ textAlign: 'center', color: 'text.secondary', py: 4 }}>
                                        Nothing recorded for these filters.
                                    </TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 1.5 }}>
                    <Typography variant="body2" color="text.secondary">Showing {items.length} of {total}</Typography>
                    {loading ? <CircularProgress size={22} /> : items.length < total && (
                        <Button size="small" onClick={() => load(page + 1, filters)}>Load more</Button>
                    )}
                </Box>
            </Paper>
        </Box>
    );
}

export default AuditLogView;
