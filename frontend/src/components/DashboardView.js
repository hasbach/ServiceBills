// In src/components/DashboardView.js

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { Box, Grid, Card, CardContent, Typography, CircularProgress, Divider, Chip, TextField, Paper } from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const toISODate = (d) => d.toISOString().split('T')[0];

// Presets compute their own [start, end] on demand rather than storing fixed
// dates, so "Last 7 Days" etc. always mean "as of right now" whenever picked.
const PRESETS = [
    { key: 'last7', label: 'Last 7 Days', range: () => {
        const end = new Date();
        const start = new Date();
        start.setDate(start.getDate() - 6);
        return [toISODate(start), toISODate(end)];
    }},
    { key: 'last30', label: 'Last 30 Days', range: () => {
        const end = new Date();
        const start = new Date();
        start.setDate(start.getDate() - 29);
        return [toISODate(start), toISODate(end)];
    }},
    { key: 'lastYear', label: 'Last Year', range: () => {
        const end = new Date();
        const start = new Date();
        start.setFullYear(start.getFullYear() - 1);
        return [toISODate(start), toISODate(end)];
    }},
    { key: 'max', label: 'Maximum', range: () => [null, null] }, // no filter = all-time
    { key: 'custom', label: 'Custom', range: null },
];

const DashboardView = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [metrics, setMetrics] = useState(null);
    const [loading, setLoading] = useState(true);
    const [activePreset, setActivePreset] = useState('max');
    const [customStart, setCustomStart] = useState(toISODate(new Date(new Date().setMonth(new Date().getMonth() - 1))));
    const [customEnd, setCustomEnd] = useState(toISODate(new Date()));

    const [startDate, endDate] = useMemo(() => {
        if (activePreset === 'custom') return [customStart, customEnd];
        const preset = PRESETS.find(p => p.key === activePreset);
        return preset ? preset.range() : [null, null];
    }, [activePreset, customStart, customEnd]);

    const fetchMetrics = useCallback(async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchDashboardMetrics(startDate, endDate);
            setMetrics(response.data);
        } catch (error) {
            console.error(error);
            setSnackbar({
                open: true,
                message: 'Failed to load dashboard metrics.',
                severity: 'error'
            });
        } finally {
            setLoading(false);
        }
    }, [apiService, setSnackbar, startDate, endDate]);

    useEffect(() => { fetchMetrics(); }, [fetchMetrics]);

    const MetricCard = ({ title, value, format = (v) => v }) => (
        <Grid item xs={12} sm={6} md={4} lg={2.4}>
            <Card sx={{ height: '100%' }}>
                <CardContent sx={{ textAlign: 'center' }}>
                    <Typography color="text.secondary" gutterBottom>
                        {title}
                    </Typography>
                    <Typography variant="h4" component="div">
                        {loading || !metrics ? <CircularProgress size={28} /> : format(value)}
                    </Typography>
                </CardContent>
            </Card>
        </Grid>
    );

    return (
        <Box>
            <Typography variant="h4" gutterBottom>Dashboard</Typography>

            {/* Date range -- scopes Revenue/Expenses below to a period; customer
                counts and outstanding balance are always "as of now" (they're a
                snapshot, not something that happened "during" a range). */}
            <Paper elevation={0} sx={{ p: 2, mb: 3, borderRadius: '16px', border: '1px solid', borderColor: 'divider' }}>
                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
                    {PRESETS.map(p => (
                        <Chip
                            key={p.key}
                            label={p.label}
                            onClick={() => setActivePreset(p.key)}
                            color={activePreset === p.key ? 'primary' : 'default'}
                            variant={activePreset === p.key ? 'filled' : 'outlined'}
                            sx={{ fontWeight: 600 }}
                        />
                    ))}
                    {activePreset === 'custom' && (
                        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', ml: 1 }}>
                            <TextField
                                type="date" label="Start" size="small" value={customStart}
                                onChange={(e) => setCustomStart(e.target.value)}
                                InputLabelProps={{ shrink: true }}
                            />
                            <TextField
                                type="date" label="End" size="small" value={customEnd}
                                onChange={(e) => setCustomEnd(e.target.value)}
                                InputLabelProps={{ shrink: true }}
                            />
                        </Box>
                    )}
                </Box>
            </Paper>

            <Typography variant="h6" sx={{ mt: 3, mb: 2, color: 'text.secondary' }}>
                Overall Metrics
            </Typography>
            <Grid container spacing={3}>
                <MetricCard title="Total Customers" value={metrics?.totalCustomers} />
                <MetricCard title="Active Customers" value={metrics?.activeCustomers} />
                <MetricCard
                    title="Total Revenue"
                    value={metrics?.totalRevenue}
                    format={(v) => `$${v.toFixed(2)}`}
                />
                <MetricCard
                    title="Total Expenses"
                    value={metrics?.totalExpenses}
                    format={(v) => `$${v.toFixed(2)}`}
                />
                <MetricCard
                    title="Outstanding Balance"
                    value={metrics?.outstandingBalance}
                    format={(v) => `$${v.toFixed(2)}`}
                />
            </Grid>

            {/* Expenses by type -- payroll and supplier cash payments were previously
                only visible buried in their own tabs, invisible next to manual expenses. */}
            <Divider sx={{ my: 4 }} />
            <Typography variant="h6" sx={{ mb: 2, color: 'text.secondary' }}>
                Expenses by Type
            </Typography>
            <Grid container spacing={3}>
                <MetricCard title="Manual Expenses" value={metrics?.manualExpenses} format={(v) => `$${v.toFixed(2)}`} />
                <MetricCard title="Supplier Payments" value={metrics?.supplierExpenses} format={(v) => `$${v.toFixed(2)}`} />
                <MetricCard title="Payroll" value={metrics?.payrollExpenses} format={(v) => `$${v.toFixed(2)}`} />
            </Grid>

            {/* --- ADDED: New section for the subscription breakdown --- */}
            {metrics?.subscriptionsBreakdown && metrics.subscriptionsBreakdown.length > 0 && (
                <>
                    <Divider sx={{ my: 4 }} />
                    <Typography variant="h6" sx={{ mb: 2, color: 'text.secondary' }}>
                        Active Subscriptions Breakdown
                    </Typography>
                    <Grid container spacing={3}>
                        {metrics.subscriptionsBreakdown.map(plan => (
                            <MetricCard
                                key={plan.plan_name}
                                title={plan.plan_name}
                                value={plan.count}
                            />
                        ))}
                    </Grid>
                </>
            )}
        </Box>
    );
};

export default DashboardView;
