import React, { useState } from 'react';
import {
    Box, Grid, Paper, Typography, Button, Card, CardContent, Collapse, IconButton,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert
} from '@mui/material';
import { KeyboardArrowDown as KeyboardArrowDownIcon, KeyboardArrowUp as KeyboardArrowUpIcon } from '@mui/icons-material';
import { apiService } from '../context/AppContext.js';

/**
 * Cash in / cash out / cash on hand for the Daily Cash report's selected day.
 * `flow` is the `cash_flow` object from GET /api/reports/daily-cash.
 */
function DailyCashFlowSection({ flow, currency, onOpeningSaved }) {
    const [expanded, setExpanded] = useState({});
    const [openingDialog, setOpeningDialog] = useState(null); // { date, amount } while editing
    const [saveError, setSaveError] = useState(null);

    const fmt = (v) => `${v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)} ${currency}`;
    const toggle = (key) => setExpanded((e) => ({ ...e, [key]: !e[key] }));

    const saveOpening = async (clear = false) => {
        setSaveError(null);
        try {
            await apiService.api.put('/reports/cash-opening', clear
                ? { date: null }
                : { date: openingDialog.date, amount: openingDialog.amount });
            setOpeningDialog(null);
            onOpeningSaved();
        } catch (err) {
            setSaveError(err.response?.data?.error || 'Could not save the opening cash.');
        }
    };

    const hasRunning = flow.cash_start !== null && flow.cash_start !== undefined;
    const cards = [
        hasRunning && { label: 'Cash at start of day', value: flow.cash_start, color: 'text.primary' },
        { label: 'Cash in', value: flow.cash_in.total, color: 'success.main', sign: '+' },
        { label: 'Cash out', value: flow.cash_out.total, color: 'error.main', sign: '−' },
        hasRunning
            ? { label: 'Cash at end of day', value: flow.cash_end, color: 'text.primary', strong: true }
            : { label: 'Net for the day', value: flow.net, color: flow.net < 0 ? 'error.main' : 'success.main', strong: true },
    ].filter(Boolean);

    const renderSection = (title, section, dir) => (
        <Grid item xs={12} md={6}>
            <Paper variant="outlined" sx={{ p: { xs: 1, sm: 2 }, height: '100%' }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                    <Typography variant="subtitle1" fontWeight={600}>{title}</Typography>
                    <Typography variant="subtitle1" fontWeight={600} color={dir === 'in' ? 'success.main' : 'error.main'}>
                        {fmt(section.total)}
                    </Typography>
                </Box>
                {section.items.length === 0 ? (
                    <Typography color="text.secondary" variant="body2">Nothing on this day.</Typography>
                ) : (
                    <TableContainer>
                        <Table size="small" sx={{ '& td, & th': { px: { xs: 0.5, sm: 2 } } }}>
                            <TableBody>
                                {section.items.map((item) => {
                                    const key = `${dir}:${item.category}`;
                                    return (
                                        <React.Fragment key={key}>
                                            <TableRow hover sx={{ cursor: 'pointer' }} onClick={() => toggle(key)}>
                                                <TableCell padding="checkbox">
                                                    <IconButton size="small">
                                                        {expanded[key] ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                                                    </IconButton>
                                                </TableCell>
                                                <TableCell>{item.category}</TableCell>
                                                <TableCell align="right" sx={{ color: 'text.secondary' }}>{item.count}</TableCell>
                                                <TableCell align="right" sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{fmt(item.total)}</TableCell>
                                            </TableRow>
                                            <TableRow>
                                                <TableCell colSpan={4} sx={{ py: 0, border: 0 }}>
                                                    <Collapse in={!!expanded[key]} timeout="auto" unmountOnExit>
                                                        <Table size="small">
                                                            <TableHead>
                                                                <TableRow>
                                                                    <TableCell>Time</TableCell>
                                                                    <TableCell>Details</TableCell>
                                                                    <TableCell align="right">Amount</TableCell>
                                                                </TableRow>
                                                            </TableHead>
                                                            <TableBody>
                                                                {item.entries.map((e, i) => (
                                                                    <TableRow key={i}>
                                                                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{e.time}</TableCell>
                                                                        <TableCell>{e.description}</TableCell>
                                                                        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>{fmt(e.amount)}</TableCell>
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
    );

    return (
        <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 1, mb: 2 }}>
                    <Typography variant="h6">Cash Flow</Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" color="text.secondary">
                            {flow.opening
                                ? `Opening cash: ${fmt(flow.opening.amount)} on ${flow.opening.date}`
                                : 'Set an opening cash amount to track cash on hand day by day.'}
                        </Typography>
                        <Button size="small" variant="outlined" onClick={() => setOpeningDialog({
                            date: flow.opening?.date || flow.day,
                            amount: flow.opening ? String(flow.opening.amount) : '',
                        })}>
                            {flow.opening ? 'Edit' : 'Set opening cash'}
                        </Button>
                    </Box>
                </Box>

                {flow.opening && !hasRunning && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                        This day is before the opening cash date ({flow.opening.date}), so cash on hand isn't tracked for it.
                    </Alert>
                )}

                <Grid container spacing={2} sx={{ mb: 2 }}>
                    {cards.map((c) => (
                        <Grid item xs={6} md={12 / cards.length} key={c.label}>
                            <Card variant="outlined">
                                <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
                                    <Typography variant="body2" color="text.secondary">{c.label}</Typography>
                                    <Typography variant={c.strong ? 'h5' : 'h6'} fontWeight={c.strong ? 700 : 600} color={c.color}
                                        sx={{ fontSize: { xs: '1.05rem', sm: undefined }, whiteSpace: 'nowrap' }}>
                                        {c.sign ? `${c.sign} ` : ''}{fmt(c.value)}
                                    </Typography>
                                </CardContent>
                            </Card>
                        </Grid>
                    ))}
                </Grid>

                <Grid container spacing={2}>
                    {renderSection('Cash in', flow.cash_in, 'in')}
                    {renderSection('Cash out', flow.cash_out, 'out')}
                </Grid>
            </Paper>

            <Dialog open={!!openingDialog} onClose={() => setOpeningDialog(null)} fullWidth maxWidth="xs">
                <DialogTitle>Opening cash</DialogTitle>
                <DialogContent dividers>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        Count the cash in the box at the start of a day and enter it here. Each later day's
                        cash on hand is this amount plus all cash in, minus all cash out, since that date.
                    </Typography>
                    {saveError && <Alert severity="error" sx={{ mb: 2 }}>{saveError}</Alert>}
                    <TextField
                        fullWidth margin="dense" label="Date" type="date" InputLabelProps={{ shrink: true }}
                        value={openingDialog?.date || ''}
                        onChange={(e) => setOpeningDialog((d) => ({ ...d, date: e.target.value }))}
                    />
                    <TextField
                        fullWidth margin="dense" label={`Cash on hand at start of that day (${currency})`} type="number"
                        inputProps={{ step: '0.01' }}
                        value={openingDialog?.amount ?? ''}
                        onChange={(e) => setOpeningDialog((d) => ({ ...d, amount: e.target.value }))}
                    />
                </DialogContent>
                <DialogActions>
                    {flow.opening && <Button color="error" onClick={() => saveOpening(true)} sx={{ mr: 'auto' }}>Remove</Button>}
                    <Button onClick={() => setOpeningDialog(null)}>Cancel</Button>
                    <Button variant="contained" disabled={!openingDialog?.date || openingDialog?.amount === ''}
                        onClick={() => saveOpening(false)}>Save</Button>
                </DialogActions>
            </Dialog>
        </Grid>
    );
}

export default DailyCashFlowSection;
