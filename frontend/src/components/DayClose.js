import React, { useState, useEffect, useCallback } from 'react';
import {
  Paper, Typography, Button, TextField, Alert, Chip, Tooltip, Box,
  Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Dialog, DialogTitle, DialogContent, DialogActions,
  Accordion, AccordionSummary, AccordionDetails,
} from '@mui/material';
import { ExpandMore as ExpandMoreIcon } from '@mui/icons-material';
import { apiService } from '../context/AppContext.js';

const apiError = (err, fallback) =>
  err?.response?.data?.error || err?.response?.data?.message || fallback;

const money = (v) => Number(v || 0).toFixed(2);
const signed = (v) => {
  const n = Math.round(Number(v || 0) * 100) / 100;
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}`;
};

// Over/short display: green over, red short, "Balanced" at zero.
export function DiffText({ value, currency }) {
  const n = Math.round(Number(value || 0) * 100) / 100;
  if (n === 0) return <Typography component="span" color="text.secondary">Balanced</Typography>;
  return (
    <Typography component="span" color={n > 0 ? 'success.main' : 'error.main'} fontWeight={600}>
      {signed(n)}{currency ? ` ${currency}` : ''} {n > 0 ? '(over)' : '(short)'}
    </Typography>
  );
}

export function CloseDayDialog({ open, day, expectedCash, expectedWhish, currency, onClose, onSaved }) {
  const [cash, setCash] = useState('');
  const [whish, setWhish] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setCash(String(money(expectedCash)));
      setWhish(String(money(expectedWhish)));
      setNote('');
      setError(null);
    }
  }, [open, expectedCash, expectedWhish]);

  const cashNum = parseFloat(cash);
  const whishNum = parseFloat(whish);
  const valid = Number.isFinite(cashNum) && Number.isFinite(whishNum);

  const submit = async () => {
    setError(null);
    setSaving(true);
    try {
      await apiService.api.post('/day-closes', {
        day, counted_cash: cashNum, counted_whish: whishNum, note: note.trim(),
      });
      onSaved();
    } catch (err) {
      setError(apiError(err, 'Could not close the day.'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <DialogTitle>Close day {day}</DialogTitle>
      <DialogContent>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Count the cash in the box and check your Whish balance. Any difference is booked as an
          over/short adjustment and the day is locked.
        </Typography>
        <Typography variant="body2">Expected cash: {money(expectedCash)} {currency}</Typography>
        <TextField fullWidth margin="dense" label="Counted cash" type="number"
          inputProps={{ step: '0.01' }} value={cash} onChange={(e) => setCash(e.target.value)} />
        <Box sx={{ mb: 1 }} data-testid="cash-diff">
          {Number.isFinite(cashNum) && <DiffText value={cashNum - Number(expectedCash || 0)} currency={currency} />}
        </Box>
        <Typography variant="body2">Expected Whish: {money(expectedWhish)} {currency}</Typography>
        <TextField fullWidth margin="dense" label="Counted Whish" type="number"
          inputProps={{ step: '0.01' }} value={whish} onChange={(e) => setWhish(e.target.value)} />
        <Box sx={{ mb: 1 }} data-testid="whish-diff">
          {Number.isFinite(whishNum) && <DiffText value={whishNum - Number(expectedWhish || 0)} currency={currency} />}
        </Box>
        <TextField fullWidth margin="dense" label="Note (optional)" value={note}
          onChange={(e) => setNote(e.target.value)} inputProps={{ maxLength: 200 }} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={submit} disabled={!valid || saving}>Close day</Button>
      </DialogActions>
    </Dialog>
  );
}

export function ClosedDayBanner({ close, currency, canReopen, onReopen, error }) {
  const late = close.late || { cash: 0, whish: 0, count: 0 };
  const closedAt = close.closed_at
    ? new Date(close.closed_at.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(close.closed_at)
      ? close.closed_at : `${close.closed_at}Z`).toLocaleString()
    : '';
  return (
    <Paper sx={{ p: 2 }} variant="outlined">
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
        <Typography variant="subtitle1" fontWeight={600}>
          Day closed by {close.closed_by} at {closedAt}
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          {late.count > 0 && (
            <Tooltip title="Late confirmations or online payments arrived after the close. They are included in the next day's starting balance.">
              <Chip color="warning" size="small"
                label={`Changed after close: Cash ${signed(late.cash)} · Whish ${signed(late.whish)}`} />
            </Tooltip>
          )}
          {canReopen && <Button size="small" color="error" variant="outlined" onClick={onReopen}>Reopen</Button>}
        </Box>
      </Box>
      {error && <Alert severity="error" sx={{ mt: 1 }}>{error}</Alert>}
      <TableContainer sx={{ mt: 1 }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell />
              <TableCell align="right">Expected</TableCell>
              <TableCell align="right">Counted</TableCell>
              <TableCell align="right">Over / short</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            <TableRow>
              <TableCell>Cash</TableCell>
              <TableCell align="right">{money(close.expected_cash)} {currency}</TableCell>
              <TableCell align="right">{money(close.counted_cash)} {currency}</TableCell>
              <TableCell align="right"><DiffText value={close.cash_diff} currency={currency} /></TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Whish</TableCell>
              <TableCell align="right">{money(close.expected_whish)} {currency}</TableCell>
              <TableCell align="right">{money(close.counted_whish)} {currency}</TableCell>
              <TableCell align="right"><DiffText value={close.whish_diff} currency={currency} /></TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </TableContainer>
      {close.note && <Typography variant="body2" sx={{ mt: 1 }}>Note: {close.note}</Typography>}
    </Paper>
  );
}

export function ClosesHistory({ refreshKey }) {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await apiService.api.get('/day-closes', { params: { limit: 30 } });
      setRows(Array.isArray(res.data) ? res.data : []);
      setError(null);
    } catch (err) {
      setRows([]);
      setError(apiError(err, 'Failed to load closes history.'));
    }
  }, []);

  useEffect(() => { load(); }, [load, refreshKey]);

  return (
    <Accordion>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Typography variant="h6">Closes history</Typography>
      </AccordionSummary>
      <AccordionDetails>
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        {rows.length === 0 ? (
          <Typography color="text.secondary">No days closed yet.</Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Day</TableCell>
                  <TableCell align="right">Cash expected</TableCell>
                  <TableCell align="right">Cash counted</TableCell>
                  <TableCell align="right">Cash ±</TableCell>
                  <TableCell align="right">Whish expected</TableCell>
                  <TableCell align="right">Whish counted</TableCell>
                  <TableCell align="right">Whish ±</TableCell>
                  <TableCell>Closed by</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{r.day}</TableCell>
                    <TableCell align="right">{money(r.expected_cash)}</TableCell>
                    <TableCell align="right">{money(r.counted_cash)}</TableCell>
                    <TableCell align="right"><DiffText value={r.cash_diff} /></TableCell>
                    <TableCell align="right">{money(r.expected_whish)}</TableCell>
                    <TableCell align="right">{money(r.counted_whish)}</TableCell>
                    <TableCell align="right"><DiffText value={r.whish_diff} /></TableCell>
                    <TableCell>{r.closed_by || '-'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </AccordionDetails>
    </Accordion>
  );
}
