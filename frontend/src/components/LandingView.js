import React from 'react';
import { Box, Typography, Button, Container, Grid, Card, CardContent, Chip, Stack } from '@mui/material';
import {
    CheckCircle as CheckIcon, Groups as GroupsIcon, WhatsApp as WhatsAppIcon,
    ReceiptLong as ReceiptIcon,
} from '@mui/icons-material';
import { Link } from 'react-router-dom';

const FEATURES = [
    { icon: <GroupsIcon color="primary" />, title: 'Customers & subscriptions', desc: 'Track subscribers, plans, resellers and balances in one place.' },
    { icon: <ReceiptIcon color="primary" />, title: 'Payments & receipts', desc: 'Automated billing cycles, payment collection, and printable receipts.' },
    { icon: <WhatsAppIcon color="primary" />, title: 'WhatsApp notifications', desc: 'Payment reminders and alerts via WhatsApp — manual or Cloud API.' },
];

const PLANS = [
    { name: 'Free', price: '$0', features: ['Up to 50 customers', 'Manual WhatsApp (deep-link)', 'Core billing & receipts'] },
    { name: 'Pro', price: 'Contact', highlighted: true, features: ['Unlimited customers', 'WhatsApp Cloud API (auto-send)', 'All features'] },
];

const LandingView = () => (
    <Box sx={{ bgcolor: 'background.default', minHeight: '100vh' }}>
        {/* Top bar */}
        <Box sx={{ display: 'flex', alignItems: 'center', px: { xs: 2, md: 6 }, py: 2 }}>
            <Typography variant="h6" sx={{ fontWeight: 800, color: 'primary.main', flexGrow: 1 }}>servicesBills</Typography>
            <Button component={Link} to="/login" sx={{ mr: 1 }}>Log in</Button>
            <Button component={Link} to="/register" variant="contained">Get started</Button>
        </Box>

        {/* Hero */}
        <Container maxWidth="md" sx={{ textAlign: 'center', py: { xs: 6, md: 10 } }}>
            <Typography variant="h3" sx={{ fontWeight: 800, mb: 2, fontSize: { xs: '2rem', md: '3rem' } }}>
                Billing & subscription management for service providers
            </Typography>
            <Typography variant="h6" sx={{ color: 'text.secondary', fontWeight: 400, mb: 4 }}>
                servicesBills helps ISPs and resellers manage customers, automate billing,
                collect payments, and notify subscribers — all in one place.
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} justifyContent="center">
                <Button component={Link} to="/register" variant="contained" size="large" sx={{ px: 4, py: 1.3 }}>
                    Start free
                </Button>
                <Button component={Link} to="/login" variant="outlined" size="large" sx={{ px: 4, py: 1.3 }}>
                    Log in
                </Button>
            </Stack>
        </Container>

        {/* Features */}
        <Container maxWidth="lg" sx={{ pb: { xs: 6, md: 10 } }}>
            <Grid container spacing={3}>
                {FEATURES.map((f) => (
                    <Grid item xs={12} md={4} key={f.title}>
                        <Card variant="outlined" sx={{ height: '100%' }}>
                            <CardContent>
                                <Box sx={{ mb: 1 }}>{f.icon}</Box>
                                <Typography variant="h6" sx={{ mb: 1 }}>{f.title}</Typography>
                                <Typography variant="body2" color="text.secondary">{f.desc}</Typography>
                            </CardContent>
                        </Card>
                    </Grid>
                ))}
            </Grid>
        </Container>

        {/* Pricing */}
        <Container maxWidth="md" sx={{ pb: { xs: 8, md: 12 } }}>
            <Typography variant="h4" align="center" sx={{ fontWeight: 800, mb: 4 }}>Simple pricing</Typography>
            <Grid container spacing={3} justifyContent="center">
                {PLANS.map((p) => (
                    <Grid item xs={12} sm={6} key={p.name}>
                        <Card variant="outlined" sx={{ height: '100%', borderColor: p.highlighted ? 'primary.main' : 'divider', borderWidth: p.highlighted ? 2 : 1 }}>
                            <CardContent>
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                                    <Typography variant="h5" sx={{ fontWeight: 800 }}>{p.name}</Typography>
                                    {p.highlighted && <Chip size="small" color="primary" label="Popular" />}
                                </Box>
                                <Typography variant="h4" sx={{ fontWeight: 800, mb: 2 }}>{p.price}</Typography>
                                <Stack spacing={1} sx={{ mb: 3 }}>
                                    {p.features.map((f) => (
                                        <Box key={f} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                            <CheckIcon fontSize="small" color="primary" />
                                            <Typography variant="body2">{f}</Typography>
                                        </Box>
                                    ))}
                                </Stack>
                                <Button component={Link} to="/register" fullWidth variant={p.highlighted ? 'contained' : 'outlined'}>
                                    Get started
                                </Button>
                            </CardContent>
                        </Card>
                    </Grid>
                ))}
            </Grid>
        </Container>

        <Box sx={{ textAlign: 'center', py: 3, color: 'text.secondary', borderTop: '1px solid', borderColor: 'divider' }}>
            <Typography variant="caption">© servicesBills</Typography>
        </Box>
    </Box>
);

export default LandingView;
