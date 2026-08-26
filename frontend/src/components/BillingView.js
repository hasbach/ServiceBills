import React, { useEffect, useState } from 'react';
import {
    Box, Typography, Card, CardContent, Button, Chip, Grid, CircularProgress, Alert,
    Dialog, DialogTitle, DialogContent, DialogActions, TextField, Stack, ToggleButtonGroup, ToggleButton,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const FEATURES = {
    free: ['Up to 50 customers', 'Manual WhatsApp (deep-link)', 'Core billing, payments & receipts'],
    pro: ['Unlimited customers', 'WhatsApp Cloud API (auto-send)', 'All servicesBills features'],
};

const WHISH_PRICES = { monthly: '$120/mo', yearly: '$1000/yr (save ~30%)' };

function planExpiryBanner(tenant) {
    if (!tenant || tenant.plan !== 'pro' || !tenant.plan_expires_at) return null;
    const expiresAt = new Date(tenant.plan_expires_at.replace(' ', 'T'));
    const daysLeft = Math.ceil((expiresAt - new Date()) / (1000 * 60 * 60 * 24));
    if (daysLeft > 5) return null;
    if (daysLeft >= 0) {
        return { severity: 'warning', message: `Pro expires in ${daysLeft} day${daysLeft === 1 ? '' : 's'} — renew now to avoid interruption.` };
    }
    const graceDaysLeft = 3 + daysLeft;
    if (graceDaysLeft > 0) {
        return { severity: 'error', message: `Pro expired — renew within ${graceDaysLeft} day${graceDaysLeft === 1 ? '' : 's'} to avoid losing access.` };
    }
    return null;
}

const BillingView = () => {
    const { apiService, setSnackbar, user } = useAppContext();
    const [tenant, setTenant] = useState(null);
    const [plans, setPlans] = useState({});
    const [stripeEnabled, setStripeEnabled] = useState(false);
    const [whishEnabled, setWhishEnabled] = useState(false);
    const [cycle, setCycle] = useState('monthly');
    const [busy, setBusy] = useState(false);
    const [contactOpen, setContactOpen] = useState(false);
    const [contact, setContact] = useState({ name: '', email: user?.email || '', phone: '', message: '' });

    useEffect(() => {
        apiService.tenantMe().then((r) => setTenant(r.data)).catch(() => setTenant({ plan: 'free', status: 'active' }));
        apiService.listPlans().then((r) => setPlans(r.data)).catch(() => setPlans({}));
        apiService.billingConfig().then((r) => {
            setStripeEnabled(!!r.data.stripe_enabled);
            setWhishEnabled(!!r.data.whish_enabled);
        }).catch(() => { setStripeEnabled(false); setWhishEnabled(false); });
        const status = new URLSearchParams(window.location.search).get('status');
        if (status === 'success') setSnackbar({ open: true, message: 'Payment received — your plan has been updated.', severity: 'success' });
        if (status === 'failed') setSnackbar({ open: true, message: 'Payment failed or was canceled.', severity: 'error' });
        if (status === 'error') setSnackbar({ open: true, message: 'Could not verify that payment. Contact support if you were charged.', severity: 'error' });
        if (status === 'cancel') setSnackbar({ open: true, message: 'Checkout canceled.', severity: 'info' });
    }, [apiService, setSnackbar]);

    const upgradeStripe = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingCheckout('pro');
            window.location.href = r.data.url;
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Checkout failed.', severity: 'error' });
            setBusy(false);
        }
    };

    const upgradeWhish = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingWhishCheckout(cycle);
            window.location.href = r.data.redirect;
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Checkout failed.', severity: 'error' });
            setBusy(false);
        }
    };

    const manage = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingPortal();
            window.location.href = r.data.url;
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not open billing portal.', severity: 'error' });
            setBusy(false);
        }
    };

    const submitContact = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingContact({ plan: 'pro', ...contact });
            setContactOpen(false);
            setSnackbar({ open: true, message: r.data.msg || 'Request sent.', severity: 'success' });
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not send request.', severity: 'error' });
        } finally {
            setBusy(false);
        }
    };

    if (!tenant) return <Box sx={{ p: 4, textAlign: 'center' }}><CircularProgress /></Box>;

    const banner = planExpiryBanner(tenant);

    return (
        <Box sx={{ p: { xs: 2, md: 3 } }}>
            <Typography variant="h5" sx={{ mb: 2 }}>Billing &amp; Plan</Typography>
            <Box sx={{ mb: 3, display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography>Current plan:</Typography>
                <Chip label={(tenant.plan || 'free').toUpperCase()} color="primary" />
                <Chip label={tenant.status} color={tenant.status === 'active' ? 'success' : 'warning'} variant="outlined" />
                {tenant.plan === 'pro' && tenant.plan_expires_at && (
                    <Typography variant="body2" color="text.secondary">expires {tenant.plan_expires_at}</Typography>
                )}
            </Box>
            {tenant.status !== 'active' && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                    Your subscription is inactive. Upgrade or contact us to restore full access.
                </Alert>
            )}
            {banner && <Alert severity={banner.severity} sx={{ mb: 2 }}>{banner.message}</Alert>}
            <Grid container spacing={2}>
                {Object.keys(plans).map((name) => (
                    <Grid item xs={12} md={6} key={name}>
                        <Card variant="outlined" sx={{
                            borderColor: tenant.plan === name ? 'primary.main' : 'divider',
                            borderWidth: tenant.plan === name ? 2 : 1,
                        }}>
                            <CardContent>
                                <Typography variant="h6" sx={{ textTransform: 'capitalize', mb: 1 }}>{name}</Typography>
                                <Box component="ul" sx={{ pl: 2, mb: 2, color: 'text.secondary' }}>
                                    {(FEATURES[name] || []).map((f, i) => <li key={i}>{f}</li>)}
                                </Box>
                                {tenant.plan === name && (
                                    <Chip label="Current plan" size="small" sx={{ mb: name === 'pro' && whishEnabled ? 1.5 : 0 }} />
                                )}
                                {name === 'pro' && (tenant.plan !== 'pro' || whishEnabled) && (
                                    <Stack spacing={1.5} sx={{ mt: tenant.plan === 'pro' ? 1.5 : 0 }}>
                                        {whishEnabled && (
                                            <>
                                                <ToggleButtonGroup size="small" value={cycle} exclusive
                                                    onChange={(e, v) => v && setCycle(v)}>
                                                    <ToggleButton value="monthly">{WHISH_PRICES.monthly}</ToggleButton>
                                                    <ToggleButton value="yearly">{WHISH_PRICES.yearly}</ToggleButton>
                                                </ToggleButtonGroup>
                                                <Button variant="contained" disabled={busy} onClick={upgradeWhish}>
                                                    {tenant.plan === 'pro' ? 'Renew via Whish' : 'Upgrade via Whish'}
                                                </Button>
                                            </>
                                        )}
                                        {tenant.plan !== 'pro' && stripeEnabled && (
                                            <Button variant="outlined" disabled={busy} onClick={upgradeStripe}>
                                                Upgrade to Pro (Stripe)
                                            </Button>
                                        )}
                                        {tenant.plan !== 'pro' && (
                                            <Button variant={whishEnabled || stripeEnabled ? 'text' : 'contained'} disabled={busy}
                                                    onClick={() => setContactOpen(true)}>
                                                Contact us to upgrade
                                            </Button>
                                        )}
                                    </Stack>
                                )}
                            </CardContent>
                        </Card>
                    </Grid>
                ))}
            </Grid>
            {stripeEnabled && tenant.plan !== 'free' && (
                <Button sx={{ mt: 3 }} variant="outlined" disabled={busy} onClick={manage}>
                    Manage subscription
                </Button>
            )}

            <Dialog open={contactOpen} onClose={() => setContactOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle>Contact us to upgrade to Pro</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                        Leave your details and we'll get in touch to complete the upgrade.
                    </Typography>
                    <Stack spacing={2} sx={{ mt: 1 }}>
                        <TextField label="Your name" value={contact.name} onChange={(e) => setContact({ ...contact, name: e.target.value })} fullWidth />
                        <TextField label="Email" value={contact.email} onChange={(e) => setContact({ ...contact, email: e.target.value })} fullWidth />
                        <TextField label="Phone" value={contact.phone} onChange={(e) => setContact({ ...contact, phone: e.target.value })} fullWidth />
                        <TextField label="Message (optional)" value={contact.message} onChange={(e) => setContact({ ...contact, message: e.target.value })} fullWidth multiline minRows={2} />
                    </Stack>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setContactOpen(false)}>Cancel</Button>
                    <Button variant="contained" disabled={busy} onClick={submitContact}>Send request</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default BillingView;
