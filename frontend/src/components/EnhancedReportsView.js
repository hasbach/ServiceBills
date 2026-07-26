import React, { useState, useEffect } from 'react';
import {
  Box,
  Container,
  Typography,
  Paper,
  Grid,
  TextField,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Card,
  CardContent,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
} from '@mui/material';
import { DatePicker } from '@mui/x-date-pickers/DatePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDateFns } from '@mui/x-date-pickers/AdapterDateFns';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  LineChart,
  Line,
} from 'recharts';

import { apiService } from '../context/AppContext.js';

const EnhancedReportsView = () => {
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setMonth(d.getMonth() - 6);
    return d;
  });
  const [endDate, setEndDate] = useState(new Date());
  const [reportType, setReportType] = useState('financial');
  const [reportData, setReportData] = useState(null);
  const [overduePayments, setOverduePayments] = useState([]);
  const [customerMetrics, setCustomerMetrics] = useState(null);

  useEffect(() => {
    fetchReportData();
    fetchOverduePayments();
    fetchCustomerMetrics();
  }, [startDate, endDate, reportType]);

  const fetchReportData = async () => {
    try {
      setReportData(null);
      if (reportType === 'financial') {
        const res = await apiService.fetchFinancialReport(startDate.toISOString(), endDate.toISOString());
        setReportData(res.data);
        return;
      }

      // Fallback for other reports
      const token = localStorage.getItem('token');
      const response = await fetch(`/api/reports/${reportType}?start_date=${startDate.toISOString()}&end_date=${endDate.toISOString()}`, {
         headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await response.json();
      setReportData(data);
    } catch (error) {
      console.error('Error fetching report data:', error);
    }
  };

  const fetchOverduePayments = async () => {
    try {
      const response = await apiService.fetchOverduePayments();
      setOverduePayments(Array.isArray(response.data) ? response.data : []);
    } catch (error) {
      console.error('Error fetching overdue payments:', error);
      setOverduePayments([]);
    }
  };

  const fetchCustomerMetrics = async () => {
    try {
      const response = await apiService.fetchCustomerNumbers();
      setCustomerMetrics(response.data);
    } catch (error) {
      console.error('Error fetching customer metrics:', error);
    }
  };

  const formatCurrency = (amount) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
    }).format(amount);
  };

  const renderRevenueChart = () => {
    if (!reportData) return null;

    const chartData = Object.entries(reportData.plan_revenue).map(([plan, amount]) => ({
      name: plan,
      amount: amount,
    }));

    return (
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis />
          <Tooltip formatter={(value) => formatCurrency(value)} />
          <Legend />
          <Bar dataKey="amount" name="Revenue" fill="#8884d8" />
        </BarChart>
      </ResponsiveContainer>
    );
  };

  const renderFinancialView = () => {
    if (!reportData || reportType !== 'financial' || !reportData.monthly_data) return null;

    return (
      <Grid item xs={12}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Financial Overview (Income vs Expenses)
          </Typography>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={reportData.monthly_data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="month" />
              <YAxis />
              <Tooltip formatter={(value) => formatCurrency(value)} />
              <Legend />
              <Bar dataKey="income" name="Income" fill="#4ade80" />
              <Bar dataKey="expenses" fill="#f87171" name="Expenses" />
              <Bar dataKey="profit" fill="#60a5fa" name="Profit" />
            </BarChart>
          </ResponsiveContainer>
          
          <Box mt={4} mb={2} display="flex" justifyContent="space-around" flexWrap="wrap">
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#f0fdf4', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="success.main">Total Income</Typography>
               <Typography variant="h5">{formatCurrency(reportData.totals.income)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#fef2f2', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="error.main">Total Expenses</Typography>
               <Typography variant="h5">{formatCurrency(reportData.totals.expenses)}</Typography>
            </Paper>
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#eff6ff', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" color="primary.main">Total Profit</Typography>
               <Typography variant="h5" fontWeight="bold">{formatCurrency(reportData.totals.profit)}</Typography>
            </Paper>
          </Box>

          <Typography variant="h6" gutterBottom sx={{ mt: 2 }}>
            Monthly Breakdown
          </Typography>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Month</TableCell>
                  <TableCell align="right">Income</TableCell>
                  <TableCell align="right">Expenses</TableCell>
                  <TableCell align="right">Profit</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {reportData.monthly_data.map((row) => (
                  <TableRow key={row.month}>
                    <TableCell>{row.month}</TableCell>
                    <TableCell align="right" sx={{ color: 'success.main' }}>{formatCurrency(row.income)}</TableCell>
                    <TableCell align="right" sx={{ color: 'error.main' }}>{formatCurrency(row.expenses)}</TableCell>
                    <TableCell align="right" sx={{ color: 'primary.main', fontWeight: 'bold' }}>{formatCurrency(row.profit)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Paper>
      </Grid>
    );
  };

  const renderOverduePaymentsTable = () => {
    return (
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Customer</TableCell>
              <TableCell>Amount</TableCell>
              <TableCell>Due Date</TableCell>
              <TableCell>Days Overdue</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {overduePayments.map((payment) => (
              <TableRow key={payment.id}>
                <TableCell>{payment.customer_name}</TableCell>
                <TableCell>{formatCurrency(payment.amount)}</TableCell>
                <TableCell>{new Date(payment.date).toLocaleDateString()}</TableCell>
                <TableCell>{payment.days_overdue}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    );
  };

  const renderCollectorProgressTable = () => {
    if (!reportData || !Array.isArray(reportData)) return null;

    return (
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Collector Name</TableCell>
              <TableCell align="right">Payments Collected</TableCell>
              <TableCell align="right">Total Amount Collected</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {reportData.map((collector, index) => (
              <TableRow key={index}>
                <TableCell>{collector.collector_name}</TableCell>
                <TableCell align="right">{collector.total_payments}</TableCell>
                <TableCell align="right">{formatCurrency(collector.total_amount)}</TableCell>
              </TableRow>
            ))}
            {reportData.length === 0 && (
              <TableRow>
                <TableCell colSpan={3} align="center" sx={{ py: 3, color: 'text.secondary' }}>No collections found for this period.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </TableContainer>
    );
  };

  const renderCustomerMetrics = () => {
    if (!customerMetrics) return null;

    return (
      <Grid container spacing={3}>
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                Total Customers
              </Typography>
              <Typography variant="h4">
                {customerMetrics.total_customers}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                Active Subscriptions
              </Typography>
              <Typography variant="h4">
                {customerMetrics.active_subscriptions}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={12} md={4}>
          <Card>
            <CardContent>
              <Typography color="textSecondary" gutterBottom>
                New Customers (This Month)
              </Typography>
              <Typography variant="h4">
                {customerMetrics.new_customers_this_month}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    );
  };

  return (
    <Container maxWidth="lg" sx={{ mt: 4, mb: 4 }}>
      <Grid container spacing={3}>
        {/* Report Controls */}
        <Grid item xs={12}>
          <Paper sx={{ p: 2 }}>
            <Grid container spacing={2} alignItems="center">
              <Grid item xs={12} md={3}>
                <FormControl fullWidth>
                  <InputLabel>Report Type</InputLabel>
                  <Select
                    value={reportType}
                    onChange={(e) => setReportType(e.target.value)}
                  >
                    <MenuItem value="financial">Financial Report</MenuItem>
                    <MenuItem value="revenue">Revenue Report</MenuItem>
                    <MenuItem value="customers">Customer Report</MenuItem>
                    <MenuItem value="payments">Payment Report</MenuItem>
                    <MenuItem value="collector-progress">Collector Progress Report</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} md={3}>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                  <DatePicker
                    label="Start Date"
                    value={startDate}
                    onChange={setStartDate}
                    renderInput={(params) => <TextField {...params} fullWidth />}
                  />
                </LocalizationProvider>
              </Grid>
              <Grid item xs={12} md={3}>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                  <DatePicker
                    label="End Date"
                    value={endDate}
                    onChange={setEndDate}
                    renderInput={(params) => <TextField {...params} fullWidth />}
                  />
                </LocalizationProvider>
              </Grid>
              <Grid item xs={12} md={3}>
                <Button
                  variant="contained"
                  fullWidth
                  onClick={fetchReportData}
                >
                  Generate Report
                </Button>
              </Grid>
            </Grid>
          </Paper>
        </Grid>

        {/* Customer Metrics */}
        <Grid item xs={12}>
          {renderCustomerMetrics()}
        </Grid>

        {/* Financial Report View */}
        {reportType === 'financial' && renderFinancialView()}

        {/* Revenue Chart */}
        {reportType === 'revenue' && (
          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>
                Revenue by Subscription Plan
              </Typography>
              {renderRevenueChart()}
              {reportData && (
                <Box mt={2}>
                  <Typography variant="h6">
                    Total Revenue: {formatCurrency(reportData.total_revenue)}
                  </Typography>
                  <Typography variant="body2" color="textSecondary">
                    Number of Payments: {reportData.payment_count}
                  </Typography>
                </Box>
              )}
            </Paper>
          </Grid>
        )}

        {/* Collector Progress Report */}
        {reportType === 'collector-progress' && (
          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>
                Collector Progress Report
              </Typography>
              {renderCollectorProgressTable()}
            </Paper>
          </Grid>
        )}

        {/* Overdue Payments */}
        <Grid item xs={12}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="h6" gutterBottom>
              Overdue Payments
            </Typography>
            {renderOverduePaymentsTable()}
          </Paper>
        </Grid>
      </Grid>
    </Container>
  );
};

export default EnhancedReportsView; 