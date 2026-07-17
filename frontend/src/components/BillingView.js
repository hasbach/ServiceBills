import React, { useEffect, useState } from 'react';
import { Box, Typography, Card, CardContent, Button, Chip, Grid, CircularProgress, Alert } from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const FEATURES = {
    free: ['Up to 50 customers', 'Manual WhatsApp (deep-link)', 'Core billing, payments & receipts'],
    pro: ['Unlimited customers', 'WhatsApp Cloud API (auto-send)', 'All servicesBills features'],
};

const BillingView = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [tenant, setTenant] = useState(null);
    const [plans, setPlans] = useState({});
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        apiService.tenantMe().then((r) => setTenant(r.data)).catch(() => setTenant({ plan: 'free', status: 'active' }));
        apiService.listPlans().then((r) => setPlans(r.data)).catch(() => setPlans({}));
        const status = new URLSearchParams(window.location.search).get('status');
        if (status === 'success') setSnackbar({ open: true, message: 'Payment received — your plan will update shortly.', severity: 'success' });
        if (status === 'cancel') setSnackbar({ open: true, message: 'Checkout canceled.', severity: 'info' });
    }, [apiService, setSnackbar]);

    const upgrade = async (plan) => {
        setBusy(true);
        try {
            const r = await apiService.billingCheckout(plan);
            window.location.href = r.data.url; // Stripe-hosted checkout
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Checkout failed.', severity: 'error' });
            setBusy(false);
        }
    };

    const manage = async () => {
        setBusy(true);
        try {
            const r = await apiService.billingPortal();
            window.location.href = r.data.url; // Stripe billing portal
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not open billing portal.', severity: 'error' });
            setBusy(false);
        }
    };

    if (!tenant) return <Box sx={{ p: 4, textAlign: 'center' }}><CircularProgress /></Box>;

    return (
        <Box sx={{ p: { xs: 2, md: 3 } }}>
            <Typography variant="h5" sx={{ mb: 2 }}>Billing &amp; Plan</Typography>
            <Box sx={{ mb: 3, display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography>Current plan:</Typography>
                <Chip label={(tenant.plan || 'free').toUpperCase()} color="primary" />
                <Chip label={tenant.status} color={tenant.status === 'active' ? 'success' : 'warning'} variant="outlined" />
            </Box>
            {tenant.status !== 'active' && (
                <Alert severity="warning" sx={{ mb: 2 }}>
                    Your subscription is inactive. Upgrade or update billing to restore full access.
                </Alert>
            )}
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
                                {tenant.plan === name
                                    ? <Chip label="Current plan" size="small" />
                                    : (name === 'pro' && (
                                        <Button variant="contained" disabled={busy} onClick={() => upgrade('pro')}>
                                            Upgrade to Pro
                                        </Button>
                                    ))}
                            </CardContent>
                        </Card>
                    </Grid>
                ))}
            </Grid>
            {tenant.plan !== 'free' && (
                <Button sx={{ mt: 3 }} variant="outlined" disabled={busy} onClick={manage}>
                    Manage subscription
                </Button>
            )}
        </Box>
    );
};

export default BillingView;
