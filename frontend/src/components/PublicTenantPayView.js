import React, { useEffect, useState } from 'react';
import {
    Box, Typography, Button, Card, CardContent, CircularProgress, Alert,
    TextField, List, ListItemButton, ListItemText, Avatar,
} from '@mui/material';
import axios from 'axios';

// Public, unauthenticated tenant-wide self-service Whish payment page. Staff
// hand this one static link out to anyone; the customer identifies
// themselves by phone rather than following a per-payment link (see
// PublicPaymentView.js for that flow). Uses the bare axios import, NOT the
// app's configured `api`/`apiService` instance -- same reasoning as
// PublicPaymentView.js: no JWT from this browser's localStorage may leak to
// a public endpoint.
//
// Logo-only branding (no per-tenant color theming -- see the 2026-08-27
// plan amendment's Branding note): everything but the logo uses
// ServiceBills' own existing theme, exactly like PublicPaymentView.js.
//
// Slug lives in the query string (/pay-business?slug=...), NOT a path
// segment (/pay/t/<slug>) -- same real bug PublicPaymentView.js's own
// comment documents (this build's relative asset paths, package.json's
// homepage: "./" for the Electron build, resolve wrong against a
// multi-segment URL). A single-segment path (/pay-business) doesn't have
// that problem.
const PublicTenantPayView = () => {
    const slug = new URLSearchParams(window.location.search).get('slug');
    const status = new URLSearchParams(window.location.search).get('status');
    const [branding, setBranding] = useState(null);
    const [brandingError, setBrandingError] = useState(false);
    const [loading, setLoading] = useState(true);
    const [step, setStep] = useState('phone'); // phone | pick | confirm | redirecting
    const [phone, setPhone] = useState('');
    const [candidates, setCandidates] = useState([]); // [{customer_id, name}, ...] -- may be >1, phone isn't unique
    const [customer, setCustomer] = useState(null); // the one picked/confirmed: {customer_id, name}
    const [amount, setAmount] = useState('');
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        axios.get(`/api/pay/t/${slug}`)
            .then((r) => setBranding(r.data))
            .catch(() => setBrandingError(true))
            .finally(() => setLoading(false));
    }, [slug]);

    const lookupPhone = async () => {
        setError(null);
        setBusy(true);
        try {
            const r = await axios.post(`/api/pay/t/${slug}/lookup`, { phone });
            const { customers } = r.data;
            if (customers.length === 1) {
                setCustomer(customers[0]);
                setStep('confirm');
            } else {
                setCandidates(customers);
                setStep('pick');
            }
        } catch (e) {
            setError('No account found for this phone number.');
        } finally {
            setBusy(false);
        }
    };

    const pickCustomer = (c) => {
        setCustomer(c);
        setStep('confirm');
    };

    const checkout = async () => {
        setError(null);
        const parsedAmount = parseFloat(amount);
        if (!parsedAmount || parsedAmount <= 0) {
            setError('Enter a valid amount.');
            return;
        }
        setStep('redirecting');
        try {
            const r = await axios.post(`/api/pay/t/${slug}/checkout`, {
                customer_id: customer.customer_id, amount: parsedAmount,
            });
            window.location.href = r.data.redirect;
        } catch (e) {
            setError(e.response?.data?.msg || 'Could not start payment. Please try again.');
            setStep('confirm');
        }
    };

    if (loading) return <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}><CircularProgress /></Box>;

    return (
        <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', bgcolor: '#f5f5f5', p: 2 }}>
            <Card sx={{ maxWidth: 420, width: '100%' }}>
                <CardContent sx={{ textAlign: 'center', py: 4 }}>
                    {brandingError ? (
                        <Alert severity="warning">This payment page is not available.</Alert>
                    ) : (
                        <>
                            <Avatar src={branding?.logo_url} sx={{ width: 56, height: 56, mx: 'auto', mb: 1 }} variant="rounded" />
                            <Typography variant="h6" sx={{ mb: 3 }}>{branding?.business_name}</Typography>

                            {status === 'success' ? (
                                <Alert severity="success">Payment received. Thank you!</Alert>
                            ) : (
                                <>
                                    {status === 'failed' && <Alert severity="error" sx={{ mb: 2 }}>Payment was not completed — you can try again below.</Alert>}
                                    {status === 'error' && <Alert severity="error" sx={{ mb: 2 }}>This payment attempt is no longer valid — please try again.</Alert>}
                                    {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

                                    {step === 'phone' && (
                                        <>
                                            <TextField
                                                fullWidth label="Your phone number" value={phone}
                                                onChange={(e) => setPhone(e.target.value)} sx={{ mb: 2 }}
                                            />
                                            <Button variant="contained" fullWidth disabled={busy || !phone} onClick={lookupPhone}>
                                                Continue
                                            </Button>
                                        </>
                                    )}

                                    {step === 'pick' && (
                                        <>
                                            <Typography variant="body2" sx={{ mb: 1 }}>Which subscription are you paying for?</Typography>
                                            <List>
                                                {candidates.map((c) => (
                                                    <ListItemButton key={c.customer_id} onClick={() => pickCustomer(c)}>
                                                        <ListItemText primary={c.name} />
                                                    </ListItemButton>
                                                ))}
                                            </List>
                                        </>
                                    )}

                                    {step === 'confirm' && (
                                        <>
                                            <Typography variant="body1" sx={{ mb: 1 }}>{customer?.name}</Typography>
                                            <TextField
                                                fullWidth label="Amount to pay" type="number" value={amount}
                                                onChange={(e) => setAmount(e.target.value)} sx={{ mb: 2 }}
                                            />
                                            <Button variant="contained" size="large" fullWidth onClick={checkout}>
                                                Pay with Whish
                                            </Button>
                                        </>
                                    )}

                                    {step === 'redirecting' && <CircularProgress />}
                                </>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>
        </Box>
    );
};

export default PublicTenantPayView;
