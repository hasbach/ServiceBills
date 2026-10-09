import React, { useEffect, useState, useCallback } from 'react';
import {
    Box, Typography, Table, TableHead, TableRow, TableCell, TableBody,
    Button, Chip, AppBar, Toolbar, CircularProgress, Paper, Alert, Stack,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField, ToggleButton, ToggleButtonGroup,
    Switch, Link, Tabs, Tab, MenuItem, InputAdornment,
} from '@mui/material';
import { WhatsApp as WhatsAppIcon } from '@mui/icons-material';
import LicensesAdmin from './LicensesAdmin.js';
import PlatformBillingAdmin, { METHOD_LABELS } from './PlatformBillingAdmin.js';
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

const PAID_MODULES = [
    { key: 'whatsapp', label: 'WhatsApp' },
    { key: 'ai_cs', label: 'AI customer service (needs WhatsApp)' },
    { key: 'network', label: 'Network' },
    { key: 'upstream_sync', label: 'Upstream sync' },
    { key: 'whish_payments', label: 'Whish customer payments' },
];
const ALWAYS_ON_MODULES = [
    { key: 'core', label: 'Core' },
    { key: 'office', label: 'Office' },
];

// Methods for a payment received outside the Whish checkout.
const MANUAL_METHODS = ['transfer', 'cash', 'whish_direct', 'other'];

// wa.me wants the full international number as digits only. Local Lebanese
// numbers (e.g. 03 123456 / 70123456) get the 961 country code.
const whatsAppDigits = (phone) => {
    let d = String(phone || '').replace(/\D/g, '');
    if (d.startsWith('00')) d = d.slice(2);
    if (d && d.length <= 8) d = `961${d.replace(/^0/, '')}`;
    return d;
};

const PhoneLink = ({ phone, caption }) => (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, whiteSpace: 'nowrap' }}>
        <Link href={`tel:${phone.replace(/\s/g, '')}`} underline="hover">{phone}</Link>
        <Link href={`https://wa.me/${whatsAppDigits(phone)}`} target="_blank" rel="noopener noreferrer"
              aria-label={`WhatsApp ${phone}`} sx={{ display: 'inline-flex' }}>
            <WhatsAppIcon sx={{ fontSize: 16, color: '#25D366' }} />
        </Link>
        {caption && <Typography variant="caption" color="text.secondary">{caption}</Typography>}
    </Box>
);

const TenantContact = ({ t }) => {
    const phones = [];
    if (t.contact_phone) phones.push({ phone: t.contact_phone, caption: null });
    if (t.settings_mobile && whatsAppDigits(t.settings_mobile) !== whatsAppDigits(t.contact_phone)) {
        phones.push({ phone: t.settings_mobile, caption: t.contact_phone ? '(settings)' : null });
    }
    const emails = [...new Set([...(t.admins || []).map((a) => a.email), t.settings_email].filter(Boolean))];
    const admin = (t.admins || [])[0];
    return (
        <Box sx={{ minWidth: 180 }}>
            {phones.length === 0 && <Typography variant="caption" color="text.secondary" display="block">no mobile</Typography>}
            {phones.map((p) => <PhoneLink key={p.phone} {...p} />)}
            {emails.map((e) => (
                <Link key={e} href={`mailto:${e}`} underline="hover" variant="body2" display="block">{e}</Link>
            ))}
            {admin && <Typography variant="caption" color="text.secondary">login: {admin.username}</Typography>}
        </Box>
    );
};

const EMPTY_PAYMENT = { amount: '', method: 'transfer', note: '' };

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
    const [payment, setPayment] = useState(EMPTY_PAYMENT);
    const [modulesTarget, setModulesTarget] = useState(null); // tenant whose modules dialog is open
    const [modulesBusy, setModulesBusy] = useState(false);
    const [tab, setTab] = useState(0);

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
        setPayment(EMPTY_PAYMENT);
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
        if (payment.amount !== '') {
            if (!(Number(payment.amount) > 0)) {
                setSnackbar({ open: true, message: 'Amount received must be greater than 0 (or leave it empty).', severity: 'error' });
                return;
            }
            extra.payment = { amount: Number(payment.amount), method: payment.method, note: payment.note };
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

    const changeModule = async (key, value) => {
        if (!modulesTarget || modulesBusy) return;
        setModulesBusy(true);
        try {
            const r = await apiService.adminSetModules(modulesTarget.id, { [key]: value });
            if (r?.data?.tenant) setModulesTarget((cur) => ({ ...cur, ...r.data.tenant }));
            load();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not update modules.', severity: 'error' });
        } finally {
            setModulesBusy(false);
        }
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
            <Tabs value={tab} onChange={(e, v) => setTab(v)} sx={{ px: { xs: 2, md: 3 } }}>
                <Tab label="Tenants" />
                <Tab label="Billing" />
                <Tab label="On-prem licenses" />
            </Tabs>
            {tab === 1 && <Box sx={{ p: { xs: 2, md: 3 } }}><PlatformBillingAdmin /></Box>}
            {tab === 2 && <Box sx={{ p: { xs: 2, md: 3 } }}><LicensesAdmin /></Box>}
            <Box sx={{ p: { xs: 2, md: 3 }, display: tab === 0 ? 'block' : 'none' }}>
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
                                    <TableCell>Contact</TableCell>
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
                                        <TableCell>
                                            {t.name}
                                            {t.created_at && (
                                                <Typography variant="caption" display="block" color="text.secondary" sx={{ whiteSpace: 'nowrap' }}>
                                                    joined {formatExpiry(t.created_at)}
                                                </Typography>
                                            )}
                                        </TableCell>
                                        <TableCell><TenantContact t={t} /></TableCell>
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
                                            <Button size="small" onClick={() => setModulesTarget(t)}>Modules…</Button>
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
                    <Typography variant="subtitle2" sx={{ mt: 3, mb: 0.5 }}>Payment received (optional)</Typography>
                    <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 1.5 }}>
                        Paid by bank transfer, cash, etc.? Record it so it shows on the Billing tab.
                        Leave empty for a free/comped grant. Whish checkout payments are recorded automatically.
                    </Typography>
                    <Stack direction="row" spacing={1.5}>
                        <TextField
                            label="Amount" type="number" size="small" sx={{ flex: 1 }}
                            value={payment.amount}
                            onChange={(e) => setPayment({ ...payment, amount: e.target.value })}
                            InputProps={{ startAdornment: <InputAdornment position="start">$</InputAdornment> }}
                            inputProps={{ min: 0, step: '0.01' }}
                        />
                        <TextField
                            select label="Method" size="small" sx={{ flex: 1 }}
                            value={payment.method}
                            onChange={(e) => setPayment({ ...payment, method: e.target.value })}
                        >
                            {MANUAL_METHODS.map((m) => <MenuItem key={m} value={m}>{METHOD_LABELS[m]}</MenuItem>)}
                        </TextField>
                    </Stack>
                    <TextField
                        label="Note (e.g. transfer reference)" size="small" fullWidth sx={{ mt: 1.5 }}
                        value={payment.note}
                        onChange={(e) => setPayment({ ...payment, note: e.target.value })}
                        inputProps={{ maxLength: 200 }}
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={closeGrantDialog} disabled={granting}>Cancel</Button>
                    <Button variant="contained" onClick={submitGrant} disabled={granting}>
                        {granting ? <CircularProgress size={20} /> : 'Grant'}
                    </Button>
                </DialogActions>
            </Dialog>

            <Dialog open={!!modulesTarget} onClose={() => setModulesTarget(null)} fullWidth maxWidth="xs">
                <DialogTitle>Modules — {modulesTarget?.name}</DialogTitle>
                <DialogContent>
                    {ALWAYS_ON_MODULES.map((m) => (
                        <Box key={m.key} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', py: 0.5 }}>
                            <Box>
                                <Typography variant="body2">{m.label}</Typography>
                                <Typography variant="caption" color="text.secondary">always on</Typography>
                            </Box>
                            <Switch checked disabled />
                        </Box>
                    ))}
                    {PAID_MODULES.map((m) => {
                        const overrides = modulesTarget?.module_overrides || {};
                        const overridden = m.key in overrides;
                        return (
                            <Box key={m.key} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', py: 0.5 }}>
                                <Box>
                                    <Typography variant="body2">{m.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                        {overridden ? 'override' : 'from plan'}
                                        {overridden && (
                                            <>
                                                {' · '}
                                                <Link component="button" type="button" variant="caption"
                                                      disabled={modulesBusy}
                                                      onClick={() => changeModule(m.key, null)}>
                                                    reset
                                                </Link>
                                            </>
                                        )}
                                    </Typography>
                                </Box>
                                <Switch
                                    checked={(modulesTarget?.modules || []).includes(m.key)}
                                    disabled={modulesBusy}
                                    onChange={(e) => changeModule(m.key, e.target.checked)}
                                />
                            </Box>
                        );
                    })}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setModulesTarget(null)}>Close</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default SuperAdminView;
