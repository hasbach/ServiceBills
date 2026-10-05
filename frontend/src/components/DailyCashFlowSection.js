import React, { useState } from 'react';
import {
    Box, Grid, Paper, Typography, Button, Card, CardContent, Collapse, IconButton,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField, Alert,
    Tabs, Tab, Chip
} from '@mui/material';
import { KeyboardArrowDown as KeyboardArrowDownIcon, KeyboardArrowUp as KeyboardArrowUpIcon } from '@mui/icons-material';
import { apiService } from '../context/AppContext.js';

/**
 * Cash & Whish in / out / on hand for the Daily Cash report's selected day.
 * `flow` is the `cash_flow` object from GET /api/reports/daily-cash.
 */
function DailyCashFlowSection({ flow, currency, onOpeningSaved }) {
    const [tab, setTab] = useState('combined'); // 'combined' | 'cash' | 'whish'
    const [expanded, setExpanded] = useState({});
    const [openingDialog, setOpeningDialog] = useState(null); // { date, amount, whish_amount } while editing
    const [saveError, setSaveError] = useState(null);

    const fmt = (v) => {
        if (v === null || v === undefined) return `0.00 ${currency}`;
        return `${v < 0 ? '-' : ''}${Math.abs(v).toFixed(2)} ${currency}`;
    };
    const toggle = (key) => setExpanded((e) => ({ ...e, [key]: !e[key] }));

    const saveOpening = async (clear = false) => {
        setSaveError(null);
        try {
            await apiService.api.put('/reports/cash-opening', clear
                ? { date: null }
                : {
                    date: openingDialog.date,
                    amount: parseFloat(openingDialog.amount) || 0,
                    whish_amount: openingDialog.whish_amount !== '' && openingDialog.whish_amount !== undefined
                        ? parseFloat(openingDialog.whish_amount)
                        : 0,
                });
            setOpeningDialog(null);
            onOpeningSaved();
        } catch (err) {
            setSaveError(err.response?.data?.error || 'Could not save the opening balances.');
        }
    };

    const cashIn = flow.cash_in || { total: 0, items: [] };
    const cashOut = flow.cash_out || { total: 0, items: [] };
    const whishIn = flow.whish_in || { total: 0, items: [] };
    const whishOut = flow.whish_out || { total: 0, items: [] };

    const totalIn = flow.total_in ?? ((cashIn.total || 0) + (whishIn.total || 0));
    const totalOut = flow.total_out ?? ((cashOut.total || 0) + (whishOut.total || 0));
    const totalNet = flow.total_net ?? ((flow.net || 0) + (flow.whish_net || 0));

    const hasRunningTotal = flow.total_start !== null && flow.total_start !== undefined;
    const hasRunningCash = flow.cash_start !== null && flow.cash_start !== undefined;
    const hasRunningWhish = flow.whish_start !== null && flow.whish_start !== undefined;

    let cards = [];
    if (tab === 'combined') {
        cards = [
            hasRunningTotal && {
                label: 'Total at start of day',
                value: flow.total_start,
                color: 'text.primary',
                subtext: `Cash: ${fmt(flow.cash_start)} · Whish: ${fmt(flow.whish_start)}`,
            },
            {
                label: 'Total in',
                value: totalIn,
                color: 'success.main',
                sign: '+',
                subtext: `Cash: ${fmt(cashIn.total)} · Whish: ${fmt(whishIn.total)}`,
            },
            {
                label: 'Total out',
                value: totalOut,
                color: 'error.main',
                sign: '−',
                subtext: `Cash: ${fmt(cashOut.total)} · Whish: ${fmt(whishOut.total)}`,
            },
            hasRunningTotal
                ? {
                    label: 'Total at end of day',
                    value: flow.total_end,
                    color: 'text.primary',
                    strong: true,
                    subtext: `Cash: ${fmt(flow.cash_end)} · Whish: ${fmt(flow.whish_end)}`,
                }
                : {
                    label: 'Total net for the day',
                    value: totalNet,
                    color: totalNet < 0 ? 'error.main' : 'success.main',
                    strong: true,
                    subtext: `Cash: ${fmt(flow.net)} · Whish: ${fmt(flow.whish_net)}`,
                },
        ].filter(Boolean);
    } else if (tab === 'cash') {
        cards = [
            hasRunningCash && { label: 'Cash at start of day', value: flow.cash_start, color: 'text.primary' },
            { label: 'Cash in', value: cashIn.total, color: 'success.main', sign: '+' },
            { label: 'Cash out', value: cashOut.total, color: 'error.main', sign: '−' },
            hasRunningCash
                ? { label: 'Cash at end of day', value: flow.cash_end, color: 'text.primary', strong: true }
                : { label: 'Net for the day', value: flow.net, color: flow.net < 0 ? 'error.main' : 'success.main', strong: true },
        ].filter(Boolean);
    } else if (tab === 'whish') {
        cards = [
            hasRunningWhish && { label: 'Whish at start of day', value: flow.whish_start, color: 'text.primary' },
            { label: 'Whish in', value: whishIn.total, color: 'success.main', sign: '+' },
            { label: 'Whish out', value: whishOut.total, color: 'error.main', sign: '−' },
            hasRunningWhish
                ? { label: 'Whish at end of day', value: flow.whish_end, color: 'text.primary', strong: true }
                : { label: 'Whish net for the day', value: flow.whish_net, color: (flow.whish_net || 0) < 0 ? 'error.main' : 'success.main', strong: true },
        ].filter(Boolean);
    }

    const renderSection = (title, section, dir, channelKey, badgeLabel) => {
        const sec = section || { total: 0, items: [] };
        return (
            <Grid item xs={12} md={6} key={`${channelKey}-${dir}`}>
                <Paper variant="outlined" sx={{ p: { xs: 1, sm: 2 }, height: '100%' }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <Typography variant="subtitle1" fontWeight={600}>{title}</Typography>
                            {badgeLabel && (
                                <Chip
                                    label={badgeLabel}
                                    size="small"
                                    sx={{
                                        height: 20,
                                        fontSize: '0.75rem',
                                        fontWeight: 600,
                                        bgcolor: channelKey === 'whish' ? 'rgba(225, 29, 72, 0.1)' : 'rgba(16, 185, 129, 0.1)',
                                        color: channelKey === 'whish' ? '#E11D48' : '#059669',
                                    }}
                                />
                            )}
                        </Box>
                        <Typography variant="subtitle1" fontWeight={600} color={dir === 'in' ? 'success.main' : 'error.main'}>
                            {fmt(sec.total)}
                        </Typography>
                    </Box>
                    {sec.items.length === 0 ? (
                        <Typography color="text.secondary" variant="body2">Nothing on this day.</Typography>
                    ) : (
                        <TableContainer>
                            <Table size="small" sx={{ '& td, & th': { px: { xs: 0.5, sm: 2 } } }}>
                                <TableBody>
                                    {sec.items.map((item) => {
                                        const key = `${channelKey}:${dir}:${item.category}`;
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
    };

    return (
        <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 1, mb: 2 }}>
                    <Typography variant="h6">Cash Flow</Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" color="text.secondary">
                            {flow.opening
                                ? `Opening: Cash ${fmt(flow.opening.amount)} · Whish ${fmt(flow.opening.whish_amount ?? 0)} on ${flow.opening.date}`
                                : 'Set opening balances to track cash & Whish on hand day by day.'}
                        </Typography>
                        <Button size="small" variant="outlined" onClick={() => setOpeningDialog({
                            date: flow.opening?.date || flow.day,
                            amount: flow.opening ? String(flow.opening.amount ?? '') : '',
                            whish_amount: flow.opening ? String(flow.opening.whish_amount ?? '') : '',
                        })}>
                            {flow.opening ? 'Edit' : 'Set opening balances'}
                        </Button>
                    </Box>
                </Box>

                <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2 }}>
                    <Tabs value={tab} onChange={(_, val) => setTab(val)} aria-label="flow channels">
                        <Tab label="Combined Total" value="combined" />
                        <Tab label="Cash" value="cash" />
                        <Tab label="Whish" value="whish" />
                    </Tabs>
                </Box>

                {flow.opening && !hasRunningTotal && (
                    <Alert severity="info" sx={{ mb: 2 }}>
                        This day is before the opening balance date ({flow.opening.date}), so balances on hand aren't tracked for it.
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
                                    {c.subtext && (
                                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                            {c.subtext}
                                        </Typography>
                                    )}
                                </CardContent>
                            </Card>
                        </Grid>
                    ))}
                </Grid>

                {tab === 'combined' && (
                    <Grid container spacing={2}>
                        {renderSection('Cash in', cashIn, 'in', 'cash', 'Cash')}
                        {renderSection('Whish in', whishIn, 'in', 'whish', 'Whish')}
                        {renderSection('Cash out', cashOut, 'out', 'cash', 'Cash')}
                        {renderSection('Whish out', whishOut, 'out', 'whish', 'Whish')}
                    </Grid>
                )}

                {tab === 'cash' && (
                    <Grid container spacing={2}>
                        {renderSection('Cash in', cashIn, 'in', 'cash', 'Cash')}
                        {renderSection('Cash out', cashOut, 'out', 'cash', 'Cash')}
                    </Grid>
                )}

                {tab === 'whish' && (
                    <Grid container spacing={2}>
                        {renderSection('Whish in', whishIn, 'in', 'whish', 'Whish')}
                        {renderSection('Whish out', whishOut, 'out', 'whish', 'Whish')}
                    </Grid>
                )}
            </Paper>

            <Dialog open={!!openingDialog} onClose={() => setOpeningDialog(null)} fullWidth maxWidth="xs">
                <DialogTitle>Opening balances</DialogTitle>
                <DialogContent dividers>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        Count the cash in the box and your Whish balance at the start of a day and enter them here. Each later day's
                        balances are these amounts plus all inflows, minus all outflows, since that date.
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
                    <TextField
                        fullWidth margin="dense" label={`Whish balance at start of that day (${currency})`} type="number"
                        inputProps={{ step: '0.01' }}
                        value={openingDialog?.whish_amount ?? ''}
                        onChange={(e) => setOpeningDialog((d) => ({ ...d, whish_amount: e.target.value }))}
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
