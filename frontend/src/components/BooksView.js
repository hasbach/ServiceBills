import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Box, Typography, Paper, CircularProgress, Table, TableBody, TableCell, TableContainer,
    TableHead, TableRow, TextField, MenuItem, Chip, Button, Alert
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const money = (v) => `${v < 0 ? '-' : ''}$${Math.abs(Number(v)).toFixed(2)}`;
// A zero side of a journal line / trial balance is left blank.
const side = (v) => (Number(v) ? money(v) : '');

const GROUPS = [
    ['asset', 'Assets'], ['liability', 'Liabilities'], ['equity', 'Equity'],
    ['income', 'Income'], ['expense', 'Expenses'],
];
const KIND_COLORS = { event: 'default', opening: 'info', correction: 'warning', unexplained: 'error' };
const PARTY_TYPE_LABELS = { customer: 'Customer', supplier: 'Supplier', employee: 'Employee', reseller: 'Reseller', upstream: 'Upstream' };
const PER_PAGE = 50;

const head = (labels, right = []) => (
    <TableHead>
        <TableRow>
            {labels.map((l, i) => <TableCell key={`${l}-${i}`} align={right.includes(l) ? 'right' : 'left'}><b>{l}</b></TableCell>)}
        </TableRow>
    </TableHead>
);

function AccountsSection({ accounts, totals, asOf, setAsOf, loading, error }) {
    return (
        <Box sx={{ mb: 4 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 1 }}>
                <Typography variant="h6">Accounts</Typography>
                <TextField size="small" type="date" label="As of" value={asOf} onChange={(e) => setAsOf(e.target.value)}
                    InputLabelProps={{ shrink: true }} />
                {loading && <CircularProgress size={20} />}
            </Box>
            {error && <Alert severity="error" sx={{ mb: 2 }}>Failed to load the accounts.</Alert>}
            <Paper>
                <TableContainer>
                    <Table size="small">
                        {head(['Account', 'Debit', 'Credit', 'Balance'], ['Debit', 'Credit', 'Balance'])}
                        <TableBody>
                            {GROUPS.map(([type, label]) => {
                                const rows = accounts.filter((a) => a.type === type);
                                if (rows.length === 0) return null;
                                return (
                                    <React.Fragment key={type}>
                                        <TableRow sx={{ bgcolor: 'action.hover' }}>
                                            <TableCell colSpan={4}><b>{label}</b></TableCell>
                                        </TableRow>
                                        {rows.map((a) => (
                                            <TableRow key={a.key}>
                                                <TableCell sx={{ pl: 4 }}>
                                                    <Typography component="span" variant="body2" color="text.secondary" sx={{ mr: 1 }}>{a.number}</Typography>
                                                    {a.name}
                                                </TableCell>
                                                <TableCell align="right">{side(a.debit)}</TableCell>
                                                <TableCell align="right">{side(a.credit)}</TableCell>
                                                <TableCell align="right">{money(a.balance)}</TableCell>
                                            </TableRow>
                                        ))}
                                    </React.Fragment>
                                );
                            })}
                            {accounts.length === 0 && !loading && (
                                <TableRow>
                                    <TableCell colSpan={4} sx={{ textAlign: 'center', color: 'text.secondary', py: 3 }}>
                                        No accounts yet.
                                    </TableCell>
                                </TableRow>
                            )}
                            {accounts.length > 0 && (
                                <TableRow>
                                    <TableCell><b>Total</b></TableCell>
                                    <TableCell align="right"><b>{money(totals.debit)}</b></TableCell>
                                    <TableCell align="right"><b>{money(totals.credit)}</b></TableCell>
                                    <TableCell />
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            </Paper>
        </Box>
    );
}

function JournalSection({ apiService, accounts }) {
    const [filters, setFilters] = useState({ start_date: '', end_date: '', account: '' });
    const [items, setItems] = useState([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const names = useMemo(() => Object.fromEntries(accounts.map((a) => [a.key, `${a.number} ${a.name}`])), [accounts]);

    const load = useCallback((pageToLoad, active) => {
        setLoading(true);
        setError(false);
        const params = { page: pageToLoad, per_page: PER_PAGE };
        Object.entries(active).forEach(([k, v]) => { if (v) params[k] = v; });
        apiService.fetchBooksJournal(params)
            .then((res) => {
                setItems((prev) => (pageToLoad === 1 ? res.data.items : [...prev, ...res.data.items]));
                setTotal(res.data.total);
                setPage(pageToLoad);
            })
            .catch(() => setError(true))
            .finally(() => setLoading(false));
    }, [apiService]);

    useEffect(() => { load(1, filters); }, [load, filters]);
    const set = (key) => (e) => setFilters((f) => ({ ...f, [key]: e.target.value }));

    return (
        <Box sx={{ mb: 4 }}>
            <Typography variant="h6" sx={{ mb: 1 }}>Journal</Typography>
            <Paper sx={{ p: 2, mb: 2, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
                <TextField size="small" type="date" label="From" value={filters.start_date} onChange={set('start_date')} InputLabelProps={{ shrink: true }} />
                <TextField size="small" type="date" label="To" value={filters.end_date} onChange={set('end_date')} InputLabelProps={{ shrink: true }} />
                <TextField select size="small" label="Account" value={filters.account} onChange={set('account')} sx={{ minWidth: 220 }}>
                    <MenuItem value="">All</MenuItem>
                    {accounts.map((a) => <MenuItem key={a.key} value={a.key}>{names[a.key]}</MenuItem>)}
                </TextField>
            </Paper>
            {error && <Alert severity="error" sx={{ mb: 2 }}>Failed to load the journal.</Alert>}
            <Paper>
                {items.map((e) => (
                    <Box key={e.id} sx={{ p: 1.5, borderBottom: 1, borderColor: 'divider' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 0.5 }}>
                            <Typography variant="body2" sx={{ fontWeight: 600 }}>{e.date}</Typography>
                            <Chip size="small" variant="outlined" color={KIND_COLORS[e.kind] || 'default'} label={e.kind} />
                            <Typography variant="body2">{e.memo}</Typography>
                            <Typography variant="body2" color="text.secondary">· {e.created_by || 'System'}</Typography>
                        </Box>
                        <Table size="small">
                            {head(['Account', 'Party', 'Debit', 'Credit'], ['Debit', 'Credit'])}
                            <TableBody>
                                {e.lines.map((l, i) => (
                                    <TableRow key={i}>
                                        <TableCell>{names[l.account] || l.account}</TableCell>
                                        <TableCell>{l.party_name || ''}</TableCell>
                                        <TableCell align="right">{side(l.debit)}</TableCell>
                                        <TableCell align="right">{side(l.credit)}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </Box>
                ))}
                {!loading && items.length === 0 && (
                    <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>
                        No entries for these filters.
                    </Typography>
                )}
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

function CheckSection({ check }) {
    const last = check && check.last_check;
    const when = (v) => (v ? new Date(v.endsWith('Z') ? v : `${v}Z`).toLocaleString() : '');
    if (!last || !last.checked_at) {
        return (
            <Box sx={{ mb: 4 }}>
                <Typography variant="h6" sx={{ mb: 1 }}>Check</Typography>
                <Typography variant="body2" color="text.secondary">The first nightly check has not run yet.</Typography>
            </Box>
        );
    }
    const mismatches = last.party_mismatches || [];
    const unbalanced = last.unbalanced_entries || [];
    const differs = (a, b) => Math.abs(Number(a) - Number(b)) >= 0.005;
    const cashRows = [['Cash', last.cash], ['Whish', last.whish]];
    const cashBad = cashRows.filter(([, v]) => v && v.cash_flow != null && differs(v.books, v.cash_flow)).length;
    const profitBad = (last.profit_by_month || []).filter((m) => differs(m.books_cash_basis, m.reports)).length;
    const toLookAt = (last.problem_count > 0 ? 1 : 0) + unbalanced.length + mismatches.length
        + (last.unexplained_count > 0 ? 1 : 0) + cashBad + profitBad;
    const agree = toLookAt === 0;
    return (
        <Box sx={{ mb: 4 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 1 }}>
                <Typography variant="h6">Check</Typography>
                <Chip size="small" color={agree ? 'success' : 'warning'} label={agree ? 'All agree' : `${toLookAt} to look at`} />
                <Typography variant="body2" color="text.secondary">Last checked {when(last.checked_at)}</Typography>
            </Box>
            {unbalanced.length > 0 && (
                <Alert severity="error" sx={{ mb: 2 }}>Entries that do not balance: {unbalanced.map((id) => `#${id}`).join(', ')}</Alert>
            )}
            {last.unexplained_count > 0 && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                    {last.unexplained_count} unexplained entr{last.unexplained_count === 1 ? 'y' : 'ies'}: a balance changed
                    with no record behind it. They are marked unexplained in the journal.
                </Alert>
            )}

            {mismatches.length > 0 && (
                <Paper sx={{ mb: 2 }}>
                    <Typography variant="subtitle2" sx={{ p: 1.5 }}>Balances that differ from the books</Typography>
                    <TableContainer>
                        <Table size="small">
                            {head(['Type', 'Name', 'Stored', 'Books'], ['Stored', 'Books'])}
                            <TableBody>
                                {mismatches.map((m) => (
                                    <TableRow key={`${m.party_type}-${m.party_id}`}>
                                        <TableCell>{PARTY_TYPE_LABELS[m.party_type] || m.party_type}</TableCell>
                                        <TableCell>{m.name || `(deleted ${m.party_type} #${m.party_id})`}</TableCell>
                                        <TableCell align="right">{money(m.stored)}</TableCell>
                                        <TableCell align="right">{money(m.books)}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </Paper>
            )}

            <Paper sx={{ mb: 2 }}>
                <Typography variant="subtitle2" sx={{ p: 1.5 }}>Cash and Whish</Typography>
                <TableContainer>
                    <Table size="small">
                        {head(['Account', 'Books', 'Cash Flow', 'Difference'], ['Books', 'Cash Flow', 'Difference'])}
                        <TableBody>
                            {cashRows.map(([label, v]) => {
                                const known = v && v.cash_flow != null;
                                const diff = known ? Number(v.books) - Number(v.cash_flow) : null;
                                return (
                                    <TableRow key={label}>
                                        <TableCell>{label}</TableCell>
                                        <TableCell align="right">{v ? money(v.books) : '—'}</TableCell>
                                        <TableCell align="right">{known ? money(v.cash_flow) : 'Unknown'}</TableCell>
                                        <TableCell align="right" sx={{ color: known && differs(v.books, v.cash_flow) ? 'warning.main' : 'text.secondary' }}>
                                            {known ? money(diff) : ''}
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </TableContainer>
            </Paper>

            {(last.profit_by_month || []).length > 0 && (
                <Paper>
                    <Typography variant="subtitle2" sx={{ p: 1.5 }}>Profit by month</Typography>
                    <TableContainer>
                        <Table size="small">
                            {head(['Month', 'Books', 'Reports', 'Difference'], ['Books', 'Reports', 'Difference'])}
                            <TableBody>
                                {last.profit_by_month.map((m) => {
                                    const diff = Number(m.books_cash_basis) - Number(m.reports);
                                    return (
                                        <TableRow key={m.month}>
                                            <TableCell>
                                                {m.month}
                                                {m.partial && <Typography component="span" variant="body2" color="text.secondary"> (from {m.from})</Typography>}
                                            </TableCell>
                                            <TableCell align="right">{money(m.books_cash_basis)}</TableCell>
                                            <TableCell align="right">{money(m.reports)}</TableCell>
                                            <TableCell align="right" sx={{ color: differs(m.books_cash_basis, m.reports) ? 'warning.main' : 'text.secondary' }}>{money(diff)}</TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </Paper>
            )}
        </Box>
    );
}

/** Read-only view of the double-entry books: trial balance, journal and the nightly check. */
function BooksView() {
    const { apiService } = useAppContext();
    const [check, setCheck] = useState(null);
    const [checkError, setCheckError] = useState(false);
    const [asOf, setAsOf] = useState('');
    const [accounts, setAccounts] = useState([]);
    const [totals, setTotals] = useState({ debit: 0, credit: 0 });
    const [accLoading, setAccLoading] = useState(true);
    const [accError, setAccError] = useState(false);

    useEffect(() => {
        apiService.fetchBooksCheck().then((res) => setCheck(res.data)).catch(() => setCheckError(true));
    }, [apiService]);

    useEffect(() => {
        setAccLoading(true);
        setAccError(false);
        apiService.fetchBooksAccounts(asOf)
            .then((res) => { setAccounts(res.data.accounts || []); setTotals(res.data.totals || { debit: 0, credit: 0 }); })
            .catch(() => setAccError(true))
            .finally(() => setAccLoading(false));
    }, [apiService, asOf]);

    const started = check && check.start_day;
    const last = check && check.last_check;

    return (
        <Box>
            {checkError && <Alert severity="error" sx={{ mb: 2 }}>Failed to load the books status.</Alert>}
            {check && (started ? (
                <Typography variant="body1" sx={{ mb: 2 }}>Books started on {check.start_day}</Typography>
            ) : (
                <Alert severity="info" sx={{ mb: 2 }}>Books start automatically tonight</Alert>
            ))}
            {last && last.cash_opening_unknown && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                    The Cash and Whish opening is 0 because no opening balance was set on Cash Flow.
                </Alert>
            )}
            {started && (
                <>
                    <AccountsSection accounts={accounts} totals={totals} asOf={asOf} setAsOf={setAsOf} loading={accLoading} error={accError} />
                    <JournalSection apiService={apiService} accounts={accounts} />
                    <CheckSection check={check} />
                </>
            )}
        </Box>
    );
}

export default BooksView;
