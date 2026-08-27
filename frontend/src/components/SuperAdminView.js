import React, { useEffect, useState, useCallback } from 'react';
import {
    Box, Typography, Table, TableHead, TableRow, TableCell, TableBody,
    Button, Chip, AppBar, Toolbar, CircularProgress, Paper, Alert, Stack,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField, ToggleButton, ToggleButtonGroup,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

// Presets shown in the grant/extend dialog. 'custom' hands plan_expires_at
// straight to the backend as an ISO date string; the others just tell the
// backend which relativedelta to apply (it computes the actual date so the
// "stack onto the existing expiry if still active" renewal semantics live
// in one place, matching a paid Whish renewal).
const DURATION_PRESETS = [
    { value: '1_month', label: '+1 month' },
    { value: '1_year', label: '+1 year' },
    { value: 'indefinite', label: 'Indefinite' },
    { value: 'custom', label: 'Custom date' },
];

const formatExpiry = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString();
};

const SuperAdminView = () => {
    const { apiService, setSnackbar, logout } = useAppContext();
    const [tenants, setTenants] = useState(null);
    const [requests, setRequests] = useState([]);
    const [grantTarget, setGrantTarget] = useState(null); // tenant being granted/extended, or null
    const [duration, setDuration] = useState('1_month');
    const [customDate, setCustomDate] = useState('');
    const [granting, setGranting] = useState(false);

    const load = useCallback(() => {
        apiService.adminTenants().then((r) => setTenants(r.data)).catch(() => setTenants([]));
        apiService.adminUpgradeRequests().then((r) => setRequests(r.data)).catch(() => setRequests([]));
    }, [apiService]);

    useEffect(() => { load(); }, [load]);

    const act = async (fn, id, label) => {
        try {
            await fn(id);
            setSnackbar({ open: true, message: `Tenant ${label}.`, severity: 'success' });
            load();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Action failed.', severity: 'error' });
        }
    };

    const setPlan = async (id, plan) => {
        try {
            await apiService.adminSetPlan(id, plan);
            setSnackbar({ open: true, message: `Plan set to ${plan}.`, severity: 'success' });
            load();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not set plan.', severity: 'error' });
        }
    };

    const openGrantDialog = (tenant) => {
        setGrantTarget(tenant);
        setDuration('1_month');
        setCustomDate('');
    };

    const submitGrant = async () => {
        if (!grantTarget) return;
        const extra = duration === 'custom'
            ? { plan_expires_at: customDate ? `${customDate}T00:00:00Z` : '' }
            : { duration };
        if (duration === 'custom' && !customDate) {
            setSnackbar({ open: true, message: 'Pick a date first.', severity: 'error' });
            return;
        }
        setGranting(true);
        try {
            await apiService.adminSetPlan(grantTarget.id, 'pro', extra);
            setSnackbar({ open: true, message: `Pro plan granted to ${grantTarget.name}.`, severity: 'success' });
            setGrantTarget(null);
            load();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not grant plan.', severity: 'error' });
        } finally {
            setGranting(false);
        }
    };

    const closeGrantDialog = () => {
        if (granting) return;
        setGrantTarget(null);
    };

    const del = (id, name) => {
        if (window.prompt(`Type DELETE to permanently remove "${name}" and ALL its data`) === 'DELETE') {
            act(apiService.adminDeleteTenant, id, 'deleted');
        }
    };

    return (
        <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
            <AppBar position="static" color="inherit">
                <Toolbar>
                    <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 800, color: 'primary.main' }}>
                        servicesBills — Platform Admin
                    </Typography>
                    <Button onClick={logout}>Logout</Button>
                </Toolbar>
            </AppBar>
            <Box sx={{ p: { xs: 2, md: 3 } }}>
                {/* Pending "contact us to upgrade" requests */}
                {requests.length > 0 && (
                    <Alert severity="info" sx={{ mb: 3 }}>
                        <Typography sx={{ fontWeight: 700, mb: 1 }}>Pending upgrade requests ({requests.length})</Typography>
                        <Stack spacing={0.5}>
                            {requests.map((r) => (
                                <Box key={r.id} sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
                                    <strong>{r.tenant_name}</strong> wants <em>{r.requested_plan}</em>
                                    <span>— {r.contact_name || '—'} · {r.contact_email || '—'} · {r.contact_phone || '—'}</span>
                                    {r.message && <span>· "{r.message}"</span>}
                                    <Button size="small" variant="contained"
                                            onClick={() => setPlan(r.tenant_id, r.requested_plan)}>
                                        Approve → set {r.requested_plan}
                                    </Button>
                                </Box>
                            ))}
                        </Stack>
                    </Alert>
                )}

                <Typography variant="h5" sx={{ mb: 2 }}>Tenants</Typography>
                {!tenants ? <CircularProgress /> : (
                    <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
                        <Table size="small">
                            <TableHead>
                                <TableRow>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Plan</TableCell>
                                    <TableCell>Status</TableCell>
                                    <TableCell align="right">Customers</TableCell>
                                    <TableCell align="right">Users</TableCell>
                                    <TableCell align="right">Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {tenants.map((t) => (
                                    <TableRow key={t.id} hover>
                                        <TableCell>{t.name}</TableCell>
                                        <TableCell>
                                            <Chip size="small" label={t.plan} />
                                            {t.plan === 'pro' && t.plan_expires_at && (
                                                <Typography variant="caption" display="block" color="text.secondary">
                                                    until {formatExpiry(t.plan_expires_at)}
                                                </Typography>
                                            )}
                                        </TableCell>
                                        <TableCell>
                                            <Chip size="small" color={t.status === 'active' ? 'success' : 'warning'} label={t.status} />
                                        </TableCell>
                                        <TableCell align="right">{t.customers}</TableCell>
                                        <TableCell align="right">{t.users}</TableCell>
                                        <TableCell align="right">
                                            <Button size="small" onClick={() => openGrantDialog(t)}>
                                                {t.plan === 'pro' ? 'Extend Pro…' : 'Grant Pro…'}
                                            </Button>
                                            {t.plan !== 'free' && <Button size="small" onClick={() => setPlan(t.id, 'free')}>Set Free</Button>}
                                            {t.status === 'active'
                                                ? <Button size="small" onClick={() => act(apiService.adminSuspendTenant, t.id, 'suspended')}>Suspend</Button>
                                                : <Button size="small" onClick={() => act(apiService.adminReactivateTenant, t.id, 'reactivated')}>Reactivate</Button>}
                                            <Button size="small" color="error" onClick={() => del(t.id, t.name)}>Delete</Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </Paper>
                )}
            </Box>

            <Dialog open={!!grantTarget} onClose={closeGrantDialog} fullWidth maxWidth="xs">
                <DialogTitle>Grant / extend Pro — {grantTarget?.name}</DialogTitle>
                <DialogContent>
                    {grantTarget?.plan === 'pro' && grantTarget?.plan_expires_at && (
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                            Currently Pro until {formatExpiry(grantTarget.plan_expires_at)}. A preset extends from that date.
                        </Typography>
                    )}
                    <ToggleButtonGroup
                        exclusive
                        fullWidth
                        value={duration}
                        onChange={(e, v) => v && setDuration(v)}
                        sx={{ mb: 2, flexWrap: 'wrap' }}
                    >
                        {DURATION_PRESETS.map((p) => (
                            <ToggleButton key={p.value} value={p.value} sx={{ flex: '1 1 40%' }}>
                                {p.label}
                            </ToggleButton>
                        ))}
                    </ToggleButtonGroup>
                    {duration === 'custom' && (
                        <TextField
                            label="Pro expires on"
                            type="date"
                            fullWidth
                            value={customDate}
                            onChange={(e) => setCustomDate(e.target.value)}
                            InputLabelProps={{ shrink: true }}
                        />
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={closeGrantDialog} disabled={granting}>Cancel</Button>
                    <Button variant="contained" onClick={submitGrant} disabled={granting}>
                        {granting ? <CircularProgress size={20} /> : 'Grant'}
                    </Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default SuperAdminView;
