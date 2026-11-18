import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box, Typography, Paper, Grid, Button, Chip, Stack, Dialog, DialogTitle, DialogContent, DialogActions,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow, IconButton, LinearProgress,
    CircularProgress, Alert, Tooltip, useMediaQuery, useTheme
} from '@mui/material';
import {
    Close as CloseIcon, ContentCopy as ContentCopyIcon, Refresh as RefreshIcon, Replay as ReplayIcon,
    StopCircle as StopCircleIcon, Download as DownloadIcon, Assessment as AssessmentIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext';

const POLL_MS = 3000;

export const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

const CAMPAIGN_STATUS = {
    queued: { label: 'Queued', color: 'default' },
    running: { label: 'Sending…', color: 'info' },
    completed: { label: 'Completed', color: 'success' },
    cancelled: { label: 'Stopped', color: 'warning' },
};

const RECIPIENT_STATUS = {
    pending: { label: 'Waiting', color: 'default' },
    sending: { label: 'Sending…', color: 'info' },
    sent: { label: 'Sent', color: 'success' },
    failed: { label: 'Failed', color: 'error' },
    skipped: { label: 'Skipped', color: 'warning' },
    cancelled: { label: 'Stopped', color: 'default' },
};

const isActive = (c) => c && (c.status === 'queued' || c.status === 'running');

const done = (c) => c.total - c.pending;

export const CampaignProgress = ({ c }) => {
    const pct = c.total ? Math.round((done(c) / c.total) * 100) : 100;
    return (
        <Box sx={{ minWidth: 120 }}>
            <LinearProgress variant="determinate" value={pct} sx={{ height: 8, borderRadius: 4 }}
                color={c.failed > 0 ? 'warning' : 'success'} />
            <Typography variant="caption" color="text.secondary">{done(c)} / {c.total}</Typography>
        </Box>
    );
};

const StatCard = ({ label, value, color, active, onClick }) => (
    <Paper elevation={0} onClick={onClick}
        sx={{
            p: 1.5, borderRadius: '14px', textAlign: 'center', cursor: onClick ? 'pointer' : 'default',
            border: active ? `2px solid ${color}` : '1px solid rgba(0,0,0,0.08)', bgcolor: '#fafafa',
        }}>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 700, textTransform: 'uppercase' }}>
            {label}
        </Typography>
        <Typography variant="h5" sx={{ fontWeight: 800, color }}>{value}</Typography>
    </Paper>
);

export const CampaignReportDialog = ({ campaignId, onClose, onChanged }) => {
    const { apiService, setSnackbar } = useAppContext();
    const [data, setData] = useState(null);
    const [filter, setFilter] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const theme = useTheme();
    const phone = useMediaQuery(theme.breakpoints.down('sm'));

    const load = useCallback(async () => {
        if (!campaignId) return;
        try {
            const res = await apiService.fetchWhatsAppCampaign(campaignId, filter || undefined);
            setData(res.data);
            setError('');
        } catch (e) {
            setError(e.response?.data?.message || 'Could not load this campaign.');
        }
    }, [apiService, campaignId, filter]);

    useEffect(() => { setData(null); setFilter(''); }, [campaignId]);
    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        if (!isActive(data)) return undefined;
        const t = setInterval(load, POLL_MS);
        return () => clearInterval(t);
    }, [data, load]);

    const act = async (fn, okMsg) => {
        setBusy(true);
        try {
            const res = await fn();
            setSnackbar({ open: true, message: res.data?.message || okMsg, severity: 'success' });
            await load();
            onChanged && onChanged();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.message || 'Action failed.', severity: 'error' });
        } finally {
            setBusy(false);
        }
    };

    const exportCsv = async () => {
        try {
            const res = await apiService.exportWhatsAppCampaign(campaignId);
            const url = URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
            const a = document.createElement('a');
            a.href = url;
            a.download = `campaign-${campaignId}.csv`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            setSnackbar({ open: true, message: 'Could not export this report.', severity: 'error' });
        }
    };

    const copyFailed = async () => {
        try {
            const res = await apiService.fetchWhatsAppCampaign(campaignId, 'failed');
            const nums = res.data.recipients.map(r => r.phone).filter(Boolean).join('\n');
            await navigator.clipboard.writeText(nums);
            setSnackbar({ open: true, message: 'Copied failed numbers to clipboard.', severity: 'info' });
        } catch (e) {
            setSnackbar({ open: true, message: 'Could not copy the failed numbers.', severity: 'error' });
        }
    };

    const toggle = (f) => setFilter(prev => (prev === f ? '' : f));
    const st = data ? (CAMPAIGN_STATUS[data.status] || { label: data.status, color: 'default' }) : null;

    return (
        <Dialog open={!!campaignId} onClose={onClose} maxWidth="md" fullWidth fullScreen={phone}
            PaperProps={{ sx: { borderRadius: phone ? 0 : '24px', p: phone ? 0 : 1 } }}>
            <DialogTitle sx={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', pb: 1, gap: 1 }}>
                <Stack direction="row" spacing={1.5} alignItems="center" sx={{ minWidth: 0, flexWrap: 'wrap', rowGap: 0.5 }}>
                    <AssessmentIcon sx={{ color: '#1a1f3a', display: { xs: 'none', sm: 'block' } }} />
                    <Box sx={{ minWidth: 0 }}>
                        <Typography variant="h6" sx={{ fontWeight: 800, color: '#1a1f3a', lineHeight: 1.3, overflowWrap: 'anywhere' }}>
                            {data ? data.title : 'Campaign report'}
                        </Typography>
                        {data && (
                            <Typography variant="caption" color="text.secondary">
                                {fmtDate(data.created_at)}{data.created_by ? ` · by ${data.created_by}` : ''}
                            </Typography>
                        )}
                    </Box>
                    {st && <Chip size="small" label={st.label} color={st.color} sx={{ fontWeight: 700 }} />}
                </Stack>
                <IconButton onClick={onClose} size="small"><CloseIcon /></IconButton>
            </DialogTitle>

            <DialogContent dividers sx={{ py: 2 }}>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
                {!data && !error && <Box sx={{ textAlign: 'center', py: 4 }}><CircularProgress /></Box>}
                {data && (
                    <>
                        {isActive(data) && (
                            <Alert severity="info" sx={{ mb: 2, borderRadius: '12px' }}>
                                Sending in the background. You can close this window or the page; it keeps going.
                            </Alert>
                        )}
                        <Box sx={{ mb: 2 }}><CampaignProgress c={data} /></Box>
                        <Grid container spacing={1.5} sx={{ mb: 2 }}>
                            <Grid item xs={4} sm={2}><StatCard label="Total" value={data.total} color="#1a1f3a" active={!filter} onClick={() => setFilter('')} /></Grid>
                            <Grid item xs={4} sm={2}><StatCard label="Sent" value={data.sent} color="#2e7d32" active={filter === 'sent'} onClick={() => toggle('sent')} /></Grid>
                            <Grid item xs={4} sm={2}><StatCard label="Delivered" value={data.delivered} color="#0277bd" active={filter === 'delivered'} onClick={() => toggle('delivered')} /></Grid>
                            <Grid item xs={4} sm={2}><StatCard label="Read" value={data.read} color="#6a1b9a" active={filter === 'read'} onClick={() => toggle('read')} /></Grid>
                            <Grid item xs={4} sm={2}><StatCard label="Failed" value={data.failed} color="#d32f2f" active={filter === 'failed'} onClick={() => toggle('failed')} /></Grid>
                            <Grid item xs={4} sm={2}><StatCard label="Waiting" value={data.pending} color="#757575" active={filter === 'pending'} onClick={() => toggle('pending')} /></Grid>
                        </Grid>
                        {(data.skipped > 0 || data.cancelled > 0) && (
                            <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
                                {data.skipped > 0 && <Chip size="small" label={`${data.skipped} skipped`} color={filter === 'skipped' ? 'warning' : 'default'} onClick={() => toggle('skipped')} />}
                                {data.cancelled > 0 && <Chip size="small" label={`${data.cancelled} stopped`} color={filter === 'cancelled' ? 'warning' : 'default'} onClick={() => toggle('cancelled')} />}
                            </Stack>
                        )}
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                            Delivered and Read come from WhatsApp receipts, which arrive a little after sending.
                        </Typography>
                        <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: '16px', maxHeight: 380 }}>
                            <Table stickyHeader size="small">
                                <TableHead>
                                    <TableRow>
                                        <TableCell sx={{ fontWeight: 700, bgcolor: '#f8f9fa' }}>Recipient</TableCell>
                                        <TableCell sx={{ fontWeight: 700, bgcolor: '#f8f9fa' }}>Name</TableCell>
                                        <TableCell sx={{ fontWeight: 700, bgcolor: '#f8f9fa' }}>Status</TableCell>
                                        <TableCell sx={{ fontWeight: 700, bgcolor: '#f8f9fa' }}>Details</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {data.recipients.length === 0 && (
                                        <TableRow><TableCell colSpan={4} sx={{ textAlign: 'center', color: 'text.secondary' }}>No numbers in this view.</TableCell></TableRow>
                                    )}
                                    {data.recipients.map(r => {
                                        const rs = RECIPIENT_STATUS[r.status] || { label: r.status, color: 'default' };
                                        return (
                                            <TableRow key={r.id} hover>
                                                <TableCell sx={{ fontWeight: 600, fontFamily: 'monospace', whiteSpace: 'nowrap' }}>{r.phone || 'N/A'}</TableCell>
                                                <TableCell>{r.name || '—'}</TableCell>
                                                <TableCell>
                                                    <Stack direction="row" spacing={0.5}>
                                                        <Chip size="small" label={rs.label} color={rs.color} sx={{ fontWeight: 700 }} />
                                                        {r.delivery_status === 'delivered' && <Chip size="small" variant="outlined" color="info" label="Delivered" />}
                                                        {r.delivery_status === 'read' && <Chip size="small" variant="outlined" color="secondary" label="Read" />}
                                                    </Stack>
                                                </TableCell>
                                                <TableCell sx={{ color: 'text.secondary', fontSize: '0.8rem' }}>
                                                    {r.error || (r.sent_at ? `Sent ${fmtDate(r.sent_at)}` : '')}
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    </>
                )}
            </DialogContent>

            <DialogActions sx={{ px: 3, py: 2, justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
                    {data && isActive(data) && (
                        <Button size="small" variant="outlined" color="warning" startIcon={<StopCircleIcon />} disabled={busy}
                            onClick={() => act(() => apiService.cancelWhatsAppCampaign(campaignId), 'Campaign stopped.')}>
                            Stop sending
                        </Button>
                    )}
                    {data && !isActive(data) && data.failed > 0 && (
                        <Button size="small" variant="outlined" color="error" startIcon={<ReplayIcon />} disabled={busy}
                            onClick={() => act(() => apiService.retryWhatsAppCampaignFailed(campaignId), 'Retrying failed numbers.')}>
                            Retry {data.failed} failed
                        </Button>
                    )}
                    {data && data.failed > 0 && (
                        <Button size="small" variant="outlined" startIcon={<ContentCopyIcon />} onClick={copyFailed}>
                            Copy failed numbers
                        </Button>
                    )}
                    {data && (
                        <Button size="small" variant="outlined" startIcon={<DownloadIcon />} onClick={exportCsv}>
                            Export CSV
                        </Button>
                    )}
                </Stack>
                <Button variant="contained" onClick={onClose} sx={{ borderRadius: '10px', fontWeight: 700 }}>Close</Button>
            </DialogActions>
        </Dialog>
    );
};

const CampaignsView = ({ openCampaignId = null, onOpened }) => {
    const { apiService } = useAppContext();
    const [campaigns, setCampaigns] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selected, setSelected] = useState(null);
    const mounted = useRef(true);

    const load = useCallback(async () => {
        try {
            const res = await apiService.fetchWhatsAppCampaigns({ limit: 100 });
            if (!mounted.current) return;
            setCampaigns(res.data.campaigns || []);
            setError('');
        } catch (e) {
            if (mounted.current) setError(e.response?.data?.message || 'Could not load campaigns.');
        } finally {
            if (mounted.current) setLoading(false);
        }
    }, [apiService]);

    useEffect(() => { mounted.current = true; load(); return () => { mounted.current = false; }; }, [load]);
    useEffect(() => {
        if (openCampaignId) {
            setSelected(openCampaignId);
            onOpened && onOpened();
        }
    }, [openCampaignId, onOpened]);
    const anyActive = campaigns.some(isActive);
    useEffect(() => {
        if (!anyActive) return undefined;
        const t = setInterval(load, POLL_MS);
        return () => clearInterval(t);
    }, [anyActive, load]);

    return (
        <Box>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="body2" color="text.secondary">
                    Every bulk send from Customer Notifications and Marketing Campaign, with its report.
                </Typography>
                <Tooltip title="Refresh">
                    <IconButton onClick={load} size="small"><RefreshIcon /></IconButton>
                </Tooltip>
            </Stack>
            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
            {loading ? (
                <Box sx={{ textAlign: 'center', py: 4 }}><CircularProgress /></Box>
            ) : campaigns.length === 0 ? (
                <Alert severity="info" sx={{ borderRadius: '12px' }}>No campaigns yet. Send one from the other tabs and its report appears here.</Alert>
            ) : (
                <TableContainer component={Paper} elevation={0} sx={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: '16px' }}>
                    <Table size="small">
                        <TableHead>
                            <TableRow>
                                <TableCell sx={{ fontWeight: 700 }}>Date</TableCell>
                                <TableCell sx={{ fontWeight: 700 }}>Campaign</TableCell>
                                <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                                <TableCell sx={{ fontWeight: 700 }}>Progress</TableCell>
                                <TableCell sx={{ fontWeight: 700 }} align="right">Sent</TableCell>
                                <TableCell sx={{ fontWeight: 700 }} align="right">Delivered</TableCell>
                                <TableCell sx={{ fontWeight: 700 }} align="right">Read</TableCell>
                                <TableCell sx={{ fontWeight: 700 }} align="right">Failed</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {campaigns.map(c => {
                                const st = CAMPAIGN_STATUS[c.status] || { label: c.status, color: 'default' };
                                return (
                                    <TableRow key={c.id} hover sx={{ cursor: 'pointer' }} onClick={() => setSelected(c.id)}>
                                        <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmtDate(c.created_at)}</TableCell>
                                        <TableCell>
                                            <Typography variant="body2" sx={{ fontWeight: 600 }}>{c.title}</Typography>
                                            <Typography variant="caption" color="text.secondary">
                                                {c.kind === 'customers' ? 'Customers' : 'Custom numbers'}{c.created_by ? ` · ${c.created_by}` : ''}
                                            </Typography>
                                        </TableCell>
                                        <TableCell><Chip size="small" label={st.label} color={st.color} sx={{ fontWeight: 700 }} /></TableCell>
                                        <TableCell><CampaignProgress c={c} /></TableCell>
                                        <TableCell align="right" sx={{ color: '#2e7d32', fontWeight: 700 }}>{c.sent}</TableCell>
                                        <TableCell align="right" sx={{ color: '#0277bd' }}>{c.delivered}</TableCell>
                                        <TableCell align="right" sx={{ color: '#6a1b9a' }}>{c.read}</TableCell>
                                        <TableCell align="right" sx={{ color: c.failed ? '#d32f2f' : 'inherit', fontWeight: c.failed ? 700 : 400 }}>{c.failed}</TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}
            <CampaignReportDialog campaignId={selected} onClose={() => setSelected(null)} onChanged={load} />
        </Box>
    );
};

export default CampaignsView;
