// In src/components/DashboardView.js

import React, { useEffect, useState } from 'react';
import { Box, Grid, Card, CardContent, Typography, CircularProgress, Divider } from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const DashboardView = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [metrics, setMetrics] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchMetrics = async () => {
            try {
                const response = await apiService.fetchDashboardMetrics();
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
        };
        fetchMetrics();
    }, [apiService, setSnackbar]);

    if (loading) {
        return <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>;
    }

    if (!metrics) {
        return <Typography>Could not load dashboard data.</Typography>;
    }

    const MetricCard = ({ title, value, format = (v) => v }) => (
        <Grid item xs={12} sm={6} md={4} lg={2.4}>
            <Card sx={{ height: '100%' }}>
                <CardContent sx={{ textAlign: 'center' }}>
                    <Typography color="text.secondary" gutterBottom>
                        {title}
                    </Typography>
                    <Typography variant="h4" component="div">
                        {format(value)}
                    </Typography>
                </CardContent>
            </Card>
        </Grid>
    );

    return (
        <Box>
            <Typography variant="h4" gutterBottom>Dashboard</Typography>

            {/* --- CHANGED: Added section titles and dividers --- */}
            <Typography variant="h6" sx={{ mt: 3, mb: 2, color: 'text.secondary' }}>
                Overall Metrics
            </Typography>
            <Grid container spacing={3}>
                <MetricCard title="Total Customers" value={metrics.totalCustomers} />
                <MetricCard title="Active Customers" value={metrics.activeCustomers} />
                <MetricCard
                    title="Total Revenue"
                    value={metrics.totalRevenue}
                    format={(v) => `$${v.toFixed(2)}`}
                />
                <MetricCard
                    title="Total Expenses"
                    value={metrics.totalExpenses}
                    format={(v) => `$${v.toFixed(2)}`}
                />
                <MetricCard
                    title="Outstanding Balance"
                    value={metrics.outstandingBalance}
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
                <MetricCard title="Manual Expenses" value={metrics.manualExpenses} format={(v) => `$${v.toFixed(2)}`} />
                <MetricCard title="Supplier Payments" value={metrics.supplierExpenses} format={(v) => `$${v.toFixed(2)}`} />
                <MetricCard title="Payroll" value={metrics.payrollExpenses} format={(v) => `$${v.toFixed(2)}`} />
            </Grid>

            {/* --- ADDED: New section for the subscription breakdown --- */}
            {metrics.subscriptionsBreakdown && metrics.subscriptionsBreakdown.length > 0 && (
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