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
  Alert,
  Collapse,
  IconButton,
} from '@mui/material';
import { KeyboardArrowDown as KeyboardArrowDownIcon, KeyboardArrowUp as KeyboardArrowUpIcon } from '@mui/icons-material';
import { localDayRange } from './dailyCashDateRange';
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
  const [cashDate, setCashDate] = useState(new Date());
  const [reportData, setReportData] = useState(null);
  const [reportError, setReportError] = useState(null);
  const [overduePayments, setOverduePayments] = useState([]);
  const [customerMetrics, setCustomerMetrics] = useState(null);
  const [expandedCashGroups, setExpandedCashGroups] = useState({});

  useEffect(() => {
    fetchReportData();
    fetchOverduePayments();
    fetchCustomerMetrics();
  }, [startDate, endDate, reportType, cashDate]);

  const fetchReportData = async () => {
    setReportData(null);
    setReportError(null);
    try {
      if (reportType === 'daily-cash') {
        const { startIso, endIso } = localDayRange(cashDate);
        const token = localStorage.getItem('token');
        const response = await fetch(`/api/reports/daily-cash?start_date=${startIso}&end_date=${endIso}`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data?.error || data?.message || `Failed to load report (HTTP ${response.status})`);
        }
        setExpandedCashGroups({});
        setReportData(data);
        return;
      }

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
      if (!response.ok) {
        throw new Error(data?.error || data?.message || `Failed to load report (HTTP ${response.status})`);
      }
      setReportData(data);
    } catch (error) {
      console.error('Error fetching report data:', error);
      setReportError(error.message || 'Failed to load report data.');
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

    const chartData = Object.entries(reportData.plan_revenue || {}).map(([plan, amount]) => ({
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
              <Tooltip formatter={(value) => (value == null ? '—' : formatCurrency(value))} />
              <Legend />
              <Bar dataKey="income" name="Income" fill="#4ade80" />
              <Bar dataKey="expenses" fill="#f87171" name="Expenses" />
              <Bar dataKey="profit" fill="#60a5fa" name="Profit" />
              <Bar dataKey="estimated_profit" fill="#c084fc" name="Estimated Profit" />
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
            <Paper elevation={3} sx={{ p: 2, textAlign: 'center', bgcolor: '#faf5ff', flex: 1, mx: 1, minWidth: '200px', mb: 2 }}>
               <Typography variant="h6" sx={{ color: '#a855f7' }}>Estimated Profit</Typography>
               <Typography variant="h5" fontWeight="bold">{formatCurrency(reportData.totals.estimated_profit)}</Typography>
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
                  <TableCell align="right">Estimated Profit</TableCell>
                  <TableCell align="right">Variance</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {reportData.monthly_data.map((row) => (
                  <TableRow key={row.month}>
                    <TableCell>{row.month}</TableCell>
                    <TableCell align="right" sx={{ color: 'success.main' }}>{formatCurrency(row.income)}</TableCell>
                    <TableCell align="right" sx={{ color: 'error.main' }}>{formatCurrency(row.expenses)}</TableCell>
                    <TableCell align="right" sx={{ color: 'primary.main', fontWeight: 'bold' }}>{formatCurrency(row.profit)}</TableCell>
                    <TableCell align="right" sx={{ color: '#a855f7' }}>{row.estimated_profit == null ? '—' : formatCurrency(row.estimated_profit)}</TableCell>
                    <TableCell align="right" sx={{ color: row.variance == null ? 'text.secondary' : (row.variance >= 0 ? 'success.main' : 'error.main'), fontWeight: 'bold' }}>
                      {row.variance == null ? '—' : formatCurrency(row.variance)}
                    </TableCell>
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

  const renderCustomerWhishPaymentsTable = () => {
    const rows = (reportData && Array.isArray(reportData.links)) ? reportData.links : [];

    return (
      <TableContainer component={Paper}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Customer</TableCell>
              <TableCell>Phone</TableCell>
              <TableCell align="right">Amount</TableCell>
              <TableCell>Currency</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Whish Transaction #</TableCell>
              <TableCell>Created</TableCell>
              <TableCell>Completed</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((link) => (
              <TableRow key={link.id}>
                <TableCell>{link.customer_name}</TableCell>
                <TableCell>{link.customer_phone}</TableCell>
                <TableCell align="right">{link.amount}</TableCell>
                <TableCell>{link.currency}</TableCell>
                <TableCell>{link.status}</TableCell>
                <TableCell>{link.whish_transaction_number || '-'}</TableCell>
                <TableCell>{link.created_at}</TableCell>
                <TableCell>{link.completed_at || '-'}</TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} align="center" sx={{ py: 3, color: 'text.secondary' }}>No Whish payment links found for this period.</TableCell>
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

  const toggleCashGroupExpanded = (key) => {
    setExpandedCashGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const renderDailyCashReport = () => {
    if (!reportData || reportType !== 'daily-cash' || !Array.isArray(reportData.groups)) return null;

    return (
      <Grid item xs={12}>
        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>
            Daily Cash Report
          </Typography>
          <Typography variant="h5" sx={{ mb: 2 }}>
            Grand Total: {reportData.grand_total.toFixed(2)} {reportData.reporting_currency}
          </Typography>
          {reportData.groups.length === 0 ? (
            <Typography color="text.secondary">No cash payments collected on this day.</Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell />
                    <TableCell>Collector</TableCell>
                    <TableCell align="right">Payments</TableCell>
                    <TableCell align="right">Total</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {reportData.groups.map((group) => {
                    const key = group.is_office ? 'office' : group.collector_id;
                    const isExpanded = !!expandedCashGroups[key];
                    return (
                      <React.Fragment key={key}>
                        <TableRow>
                          <TableCell>
                            <IconButton size="small" onClick={() => toggleCashGroupExpanded(key)}>
                              {isExpanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                            </IconButton>
                          </TableCell>
                          <TableCell>{group.collector_name}</TableCell>
                          <TableCell align="right">{group.payment_count}</TableCell>
                          <TableCell align="right">{group.total.toFixed(2)} {reportData.reporting_currency}</TableCell>
                        </TableRow>
                        <TableRow>
                          <TableCell colSpan={4} sx={{ py: 0, border: 0 }}>
                            <Collapse in={isExpanded} timeout="auto" unmountOnExit>
                              <Table size="small">
                                <TableHead>
                                  <TableRow>
                                    <TableCell>Customer</TableCell>
                                    <TableCell align="right">Amount</TableCell>
                                    <TableCell>Currency</TableCell>
                                    <TableCell align="right">Reporting Amount</TableCell>
                                    <TableCell>Time</TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {group.payments.map((p) => (
                                    <TableRow key={p.id}>
                                      <TableCell>{p.customer_name}</TableCell>
                                      <TableCell align="right">{p.amount.toFixed(2)}</TableCell>
                                      <TableCell>{p.currency}</TableCell>
                                      <TableCell align="right">{p.reporting_amount.toFixed(2)}</TableCell>
                                      <TableCell>{p.time}</TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </Collapse>
                          </TableCell>
                        </TableRow>
                      </React.Fragment>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </Paper>
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
                    onChange={(e) => {
                      const newType = e.target.value;
                      setReportType(newType);
                      if (newType === 'daily-cash') {
                        setCashDate(new Date());
                      }
                    }}
                  >
                    <MenuItem value="financial">Financial Report</MenuItem>
                    <MenuItem value="revenue">Revenue Report</MenuItem>
                    <MenuItem value="customers">Customer Report</MenuItem>
                    <MenuItem value="payments">Payment Report</MenuItem>
                    <MenuItem value="collector-progress">Collector Progress Report</MenuItem>
                    <MenuItem value="customer-whish-payments">Customer Whish Payments Report</MenuItem>
                    <MenuItem value="daily-cash">Daily Cash Report</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} md={3}>
                <LocalizationProvider dateAdapter={AdapterDateFns}>
                  <DatePicker
                    label={reportType === 'daily-cash' ? 'Date' : 'Start Date'}
                    value={reportType === 'daily-cash' ? cashDate : startDate}
                    onChange={reportType === 'daily-cash' ? setCashDate : setStartDate}
                    renderInput={(params) => <TextField {...params} fullWidth />}
                  />
                </LocalizationProvider>
              </Grid>
              {reportType !== 'daily-cash' && (
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
              )}
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

        {/* Report Error */}
        {reportError && (
          <Grid item xs={12}>
            <Alert severity="error">{reportError}</Alert>
          </Grid>
        )}

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

        {/* Customer Whish Payments Report */}
        {reportType === 'customer-whish-payments' && (
          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>
                Customer Whish Payments Report
              </Typography>
              {renderCustomerWhishPaymentsTable()}
            </Paper>
          </Grid>
        )}

        {/* Daily Cash Report */}
        {reportType === 'daily-cash' && renderDailyCashReport()}

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