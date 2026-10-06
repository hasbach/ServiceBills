import React, { useState, useEffect, useCallback } from 'react';
import {
  Grid, Paper, Typography, Button, TextField, Alert, Collapse, IconButton,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Dialog, DialogTitle, DialogContent, DialogActions, MenuItem,
} from '@mui/material';
import {
  KeyboardArrowDown as KeyboardArrowDownIcon,
  KeyboardArrowUp as KeyboardArrowUpIcon,
  Delete as DeleteIcon,
} from '@mui/icons-material';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import { localDayRange } from './dailyCashDateRange';
import DailyCashFlowSection from './DailyCashFlowSection';
import { apiService, useAppContext } from '../context/AppContext.js';

const ACCOUNTS = [
  { value: 'cash', label: 'Cash' },
  { value: 'whish', label: 'Whish' },
];
const accountLabel = (v) => (ACCOUNTS.find((a) => a.value === v) || { label: v }).label;
const opposite = (v) => (v === 'cash' ? 'whish' : 'cash');

export const toIsoDay = (d) => {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const apiError = (err, fallback) =>
  err?.response?.data?.error || err?.response?.data?.message || fallback;

export function CashInDialog({ open, defaultDate, onClose, onSaved }) {
  const [account, setAccount] = useState('cash');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [date, setDate] = useState(defaultDate);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setAccount('cash'); setAmount(''); setReason(''); setDate(defaultDate); setError(null);
    }
  }, [open, defaultDate]);

  const submit = async () => {
    setError(null);
    setSaving(true);
    try {
      await apiService.api.post('/cash-entries', {
        account, amount: parseFloat(amount), reason: reason.trim(), date,
      });
      onSaved();
    } catch (err) {
      setError(apiError(err, 'Could not save the cash-in entry.'));
    } finally {
      setSaving(false);
    }
  };

  const valid = parseFloat(amount) > 0 && reason.trim() && date;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Cash in</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        <TextField select fullWidth margin="dense" label="Account" value={account}
          onChange={(e) => setAccount(e.target.value)}>
          {ACCOUNTS.map((a) => <MenuItem key={a.value} value={a.value}>{a.label}</MenuItem>)}
        </TextField>
        <TextField fullWidth margin="dense" label="Amount" type="number"
          inputProps={{ step: '0.01', min: '0.01' }} value={amount}
          onChange={(e) => setAmount(e.target.value)} />
        <TextField fullWidth margin="dense" label="Reason" required value={reason}
          onChange={(e) => setReason(e.target.value)} inputProps={{ maxLength: 200 }} />
        <TextField fullWidth margin="dense" label="Date" type="date" value={date}
          InputLabelProps={{ shrink: true }} onChange={(e) => setDate(e.target.value)} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={!valid || saving}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

export function TransferDialog({ open, defaultDate, onClose, onSaved }) {
  const [from, setFrom] = useState('cash');
  const [to, setTo] = useState('whish');
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [date, setDate] = useState(defaultDate);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setFrom('cash'); setTo('whish'); setAmount(''); setNote(''); setDate(defaultDate); setError(null);
    }
  }, [open, defaultDate]);

  // Keep the two accounts different: changing one flips the other when they collide.
  const changeFrom = (v) => { setFrom(v); if (v === to) setTo(opposite(v)); };
  const changeTo = (v) => { setTo(v); if (v === from) setFrom(opposite(v)); };

  const submit = async () => {
    setError(null);
    setSaving(true);
    try {
      await apiService.api.post('/account-transfers', {
        from_account: from, to_account: to, amount: parseFloat(amount), note: note.trim(), date,
      });
      onSaved();
    } catch (err) {
      setError(apiError(err, 'Could not save the transfer.'));
    } finally {
      setSaving(false);
    }
  };

  const valid = from !== to && parseFloat(amount) > 0 && date;

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Transfer between accounts</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        <TextField select fullWidth margin="dense" label="From account" value={from}
          onChange={(e) => changeFrom(e.target.value)}>
          {ACCOUNTS.map((a) => <MenuItem key={a.value} value={a.value}>{a.label}</MenuItem>)}
        </TextField>
        <TextField select fullWidth margin="dense" label="To account" value={to}
          onChange={(e) => changeTo(e.target.value)}>
          {ACCOUNTS.map((a) => <MenuItem key={a.value} value={a.value}>{a.label}</MenuItem>)}
        </TextField>
        <TextField fullWidth margin="dense" label="Amount" type="number"
          inputProps={{ step: '0.01', min: '0.01' }} value={amount}
          onChange={(e) => setAmount(e.target.value)} />
        <TextField fullWidth margin="dense" label="Note" value={note}
          onChange={(e) => setNote(e.target.value)} inputProps={{ maxLength: 200 }} />
        <TextField fullWidth margin="dense" label="Date" type="date" value={date}
          InputLabelProps={{ shrink: true }} onChange={(e) => setDate(e.target.value)} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={!valid || saving}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

function CashFlowRegister() {
  const { user } = useAppContext();
  const isAdmin = (user?.role || '').split(',').map((r) => r.trim()).includes('admin');

  const [day, setDay] = useState(new Date());
  const [reportData, setReportData] = useState(null);
  const [reportError, setReportError] = useState(null);
  const [entries, setEntries] = useState([]);
  const [transfers, setTransfers] = useState([]);
  const [expandedCashGroups, setExpandedCashGroups] = useState({});
  const [dialog, setDialog] = useState(null); // 'cash-in' | 'transfer' | null
  const [listError, setListError] = useState(null);

  const loadAll = useCallback(async () => {
    if (!(day instanceof Date) || Number.isNaN(day.getTime())) return;
    const { startIso, endIso } = localDayRange(day);
    const params = { start_date: startIso, end_date: endIso };
    setReportError(null);
    setListError(null);
    try {
      const res = await apiService.api.get('/reports/daily-cash', { params });
      setExpandedCashGroups({});
      setReportData(res.data);
    } catch (err) {
      setReportData(null);
      setReportError(apiError(err, err?.message || 'Failed to load the cash flow.'));
    }
    try {
      // Entries/transfers store a typed calendar date, so filter by the local day itself
      // (the UTC-shifted range above would start on the previous calendar day).
      const dayParams = { start_date: toIsoDay(day), end_date: toIsoDay(day) };
      const [e, t] = await Promise.all([
        apiService.api.get('/cash-entries', { params: dayParams }),
        apiService.api.get('/account-transfers', { params: dayParams }),
      ]);
      setEntries(Array.isArray(e.data) ? e.data : []);
      setTransfers(Array.isArray(t.data) ? t.data : []);
    } catch (err) {
      setEntries([]);
      setTransfers([]);
      setListError(apiError(err, 'Failed to load manual entries and transfers.'));
    }
  }, [day]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const closeAndReload = () => { setDialog(null); loadAll(); };

  const remove = async (kind, id) => {
    const label = kind === 'entry' ? 'this cash-in entry' : 'this transfer';
    if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return;
    try {
      await apiService.api.delete(`/${kind === 'entry' ? 'cash-entries' : 'account-transfers'}/${id}`);
      loadAll();
    } catch (err) {
      setListError(apiError(err, 'Could not delete.'));
    }
  };

  const toggleGroup = (key) => setExpandedCashGroups((p) => ({ ...p, [key]: !p[key] }));
  const dayIso = toIsoDay(day instanceof Date && !Number.isNaN(day.getTime()) ? day : new Date());

  const rows = [
    ...entries.map((e) => ({
      kind: 'entry', id: e.id, type: 'Cash in',
      detail: `${accountLabel(e.account)} - ${e.reason}`, amount: e.amount, by: e.created_by,
    })),
    ...transfers.map((t) => ({
      kind: 'transfer', id: t.id, type: 'Transfer',
      detail: `${accountLabel(t.from_account)} to ${accountLabel(t.to_account)}${t.note ? ` - ${t.note}` : ''}`,
      amount: t.amount, by: t.created_by,
    })),
  ];

  return (
    <Grid container spacing={3}>
      <Grid item xs={12}>
        <Paper sx={{ p: 2 }}>
          <Grid container spacing={2} alignItems="center">
            <Grid item xs={12} md={3}>
              <LocalizationProvider dateAdapter={AdapterDateFns}>
                <DatePicker
                  label="Date"
                  value={day}
                  onChange={setDay}
                  renderInput={(params) => <TextField {...params} fullWidth />}
                />
              </LocalizationProvider>
            </Grid>
            <Grid item xs={6} md={2}>
              <Button variant="contained" fullWidth onClick={() => setDialog('cash-in')}>Cash in</Button>
            </Grid>
            <Grid item xs={6} md={2}>
              <Button variant="outlined" fullWidth onClick={() => setDialog('transfer')}>Transfer</Button>
            </Grid>
          </Grid>
        </Paper>
      </Grid>

      {reportError && <Grid item xs={12}><Alert severity="error">{reportError}</Alert></Grid>}

      {reportData && reportData.cash_flow && (
        <Grid item xs={12}>
          <DailyCashFlowSection
            flow={reportData.cash_flow}
            currency={reportData.reporting_currency}
            onOpeningSaved={loadAll}
          />
        </Grid>
      )}

      <Grid item xs={12}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>Manual entries &amp; transfers</Typography>
          {listError && <Alert severity="error" sx={{ mb: 1 }}>{listError}</Alert>}
          {rows.length === 0 ? (
            <Typography color="text.secondary">No manual entries or transfers on this day.</Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Type</TableCell>
                    <TableCell>Details</TableCell>
                    <TableCell align="right">Amount</TableCell>
                    <TableCell>By</TableCell>
                    {isAdmin && <TableCell />}
                  </TableRow>
                </TableHead>
                <TableBody>
                  {rows.map((r) => (
                    <TableRow key={`${r.kind}-${r.id}`}>
                      <TableCell>{r.type}</TableCell>
                      <TableCell>{r.detail}</TableCell>
                      <TableCell align="right">{Number(r.amount).toFixed(2)}</TableCell>
                      <TableCell>{r.by || '-'}</TableCell>
                      {isAdmin && (
                        <TableCell align="right">
                          <IconButton size="small" aria-label={`Delete ${r.kind}`}
                            onClick={() => remove(r.kind, r.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
      </Grid>

      {reportData && Array.isArray(reportData.groups) && (
        <Grid item xs={12}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="h6" gutterBottom>Customer Cash by Collector</Typography>
            <Typography variant="h5" sx={{ mb: 2 }}>
              Grand Total: {reportData.grand_total.toFixed(2)} {reportData.reporting_currency}
            </Typography>
            {reportData.groups.length === 0 ? (
              <Typography color="text.secondary">No cash payments collected on this day.</Typography>
            ) : (
              <TableContainer>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell />
                      <TableCell>Collector</TableCell>
                      <TableCell align="right">Payments</TableCell>
                      <TableCell align="right">Total</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {reportData.groups.map((group) => {
                      const key = group.is_office ? 'office' : group.collector_id;
                      const isExpanded = !!expandedCashGroups[key];
                      return (
                        <React.Fragment key={key}>
                          <TableRow>
                            <TableCell>
                              <IconButton size="small" onClick={() => toggleGroup(key)}>
                                {isExpanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                              </IconButton>
                            </TableCell>
                            <TableCell>{group.collector_name}</TableCell>
                            <TableCell align="right">{group.payment_count}</TableCell>
                            <TableCell align="right">{group.total.toFixed(2)} {reportData.reporting_currency}</TableCell>
                          </TableRow>
                          <TableRow>
                            <TableCell colSpan={4} sx={{ py: 0, border: 0 }}>
                              <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                                <Table size="small">
                                  <TableHead>
                                    <TableRow>
                                      <TableCell>Customer</TableCell>
                                      <TableCell align="right">Amount</TableCell>
                                      <TableCell>Currency</TableCell>
                                      <TableCell align="right">Reporting Amount</TableCell>
                                      <TableCell>Time</TableCell>
                                    </TableRow>
                                  </TableHead>
                                  <TableBody>
                                    {group.payments.map((p) => (
                                      <TableRow key={p.id}>
                                        <TableCell>{p.customer_name}</TableCell>
                                        <TableCell align="right">{p.amount.toFixed(2)}</TableCell>
                                        <TableCell>{p.currency}</TableCell>
                                        <TableCell align="right">{p.reporting_amount.toFixed(2)}</TableCell>
                                        <TableCell>{p.time}</TableCell>
                                      </TableRow>
                                    ))}
                                  </TableBody>
                                </Table>
                              </Collapse>
                            </TableCell>
                          </TableRow>
                        </React.Fragment>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </Paper>
        </Grid>
      )}

      <CashInDialog open={dialog === 'cash-in'} defaultDate={dayIso}
        onClose={() => setDialog(null)} onSaved={closeAndReload} />
      <TransferDialog open={dialog === 'transfer'} defaultDate={dayIso}
        onClose={() => setDialog(null)} onSaved={closeAndReload} />
    </Grid>
  );
}

export default CashFlowRegister;
