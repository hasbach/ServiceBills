import React, { useEffect, useState } from 'react';
import { Box, Typography, Button, Card, CardContent, CircularProgress, Alert } from '@mui/material';
import axios from 'axios';

// Public, unauthenticated payment page opened from a WhatsApp/email link.
// Deliberately: no logo, no tenant color theming, no AppBar -- neutral
// ServiceBills branding (spec's Resolved decision #7). Uses the bare axios
// import, NOT the app's configured `api`/`apiService` instance -- that
// instance's request interceptor (see AppContext.js) attaches whatever JWT
// happens to be sitting in this browser's localStorage, which must never be
// sent to this public endpoint (e.g. if the tenant's own staff member opens
// this link from the same browser they're logged into ServiceBills with).
//
// Token lives in the query string (/pay?token=...), NOT a path segment
// (/pay/<token>) -- matches VerifyEmailView/ResetPasswordView's existing
// convention for this exact kind of public deep link, and avoids a real bug
// verified in a real browser: this build's relative asset paths
// (package.json's homepage: "./", needed for the Electron build) resolve
// ./static/js/... wrong against a two-segment URL like /pay/<token>,
// loading a blank page. A single-segment path (/pay) doesn't have that
// problem, exactly like /verify doesn't.
const PublicPaymentView = () => {
    const token = new URLSearchParams(window.location.search).get('token');
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const status = new URLSearchParams(window.location.search).get('status');

    useEffect(() => {
        axios.get(`/api/pay/${token}`)
            .then((r) => setData(r.data))
            .catch(() => setData({ valid: false, message: 'Something went wrong loading this payment link.' }))
            .finally(() => setLoading(false));
    }, [token]);

    const pay = async () => {
        setBusy(true);
        setError(null);
        try {
            const r = await axios.post(`/api/pay/${token}/checkout`);
            window.location.href = r.data.redirect;
        } catch (e) {
            setError(e.response?.data?.msg || 'Could not start checkout. Please try again.');
            setBusy(false);
        }
    };

    if (loading) return <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}><CircularProgress /></Box>;

    return (
        <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', bgcolor: '#f5f5f5', p: 2 }}>
            <Card sx={{ maxWidth: 420, width: '100%' }}>
                <CardContent sx={{ textAlign: 'center', py: 4 }}>
                    <Typography variant="h6" sx={{ mb: 2 }}>ServiceBills Payment</Typography>
                    {!data?.valid ? (
                        <Alert severity="warning">{data?.message || 'This payment link is no longer valid.'}</Alert>
                    ) : status === 'success' || data.status === 'succeeded' ? (
                        <Alert severity="success">Payment received. Thank you!</Alert>
                    ) : (
                        <>
                            <Typography variant="body1" sx={{ mb: 1 }}>{data.customer_name}</Typography>
                            <Typography variant="h4" sx={{ mb: 3 }}>{data.amount.toFixed(2)} {data.currency}</Typography>
                            {status === 'failed' && <Alert severity="error" sx={{ mb: 2 }}>Payment was not completed — you can try again below.</Alert>}
                            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
                            <Button variant="contained" size="large" disabled={busy} onClick={pay}>
                                {busy ? 'Redirecting…' : 'Pay with Whish'}
                            </Button>
                        </>
                    )}
                </CardContent>
            </Card>
        </Box>
    );
};

export default PublicPaymentView;
