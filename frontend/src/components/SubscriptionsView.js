import React, { useState, useEffect, useCallback } from 'react';
import {
    Box,
    Typography,
    Paper,
    Button,
    Dialog,
    Card,
    CardContent,
    Chip,
    Fade,
    Slide,
    Grid,
    Divider,
    alpha,
    useTheme,
    TextField,
    MenuItem,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Collapse,
    Avatar,
    DialogTitle,
    DialogContent,
    DialogActions,
    Pagination,
    CircularProgress,
    // --- NEW IMPORTS ---
    ToggleButton,
    ToggleButtonGroup,
    Checkbox,
    Toolbar,
    Tooltip,
    IconButton,
    Switch,
    FormControlLabel
} from '@mui/material';
import {
    Add as AddIcon,
    Person as PersonIcon,
    Phone as PhoneIcon,
    LocationOn as LocationOnIcon,
    Visibility as VisibilityIcon,
    VisibilityOff as VisibilityOffIcon,
    Delete as DeleteIcon,
    Refresh as RefreshIcon,
    Cancel as CancelIcon,
    CheckCircle as CheckCircleIcon,
    PlayArrow as PlayArrowIcon,
    TrendingUp as TrendingUpIcon,
    Group as GroupIcon,
    Edit as EditIcon,
    Search as SearchIcon,
    // --- NEW ICONS ---
    ViewList as ViewListIcon,
    ViewModule as ViewModuleIcon,
    Chat as ChatIcon,
    Download as DownloadIcon,
    Receipt as ReceiptIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';

// --- NEW: Toolbar for bulk actions ---
const EnhancedTableToolbar = ({ numSelected, onRenew, onCancel, onDelete }) => {
    const theme = useTheme();
    return (
        <Toolbar
            sx={{
                pl: { sm: 2 },
                pr: { xs: 1, sm: 1 },
                ...(numSelected > 0 && {
                    bgcolor: alpha(theme.palette.primary.main, theme.palette.action.activatedOpacity),
                }),
                borderRadius: '12px',
                mb: 2,
            }}
        >
            {numSelected > 0 ? (
                <Typography
                    sx={{ flex: '1 1 100%' }}
                    color="inherit"
                    variant="subtitle1"
                    component="div"
                >
                    {numSelected} selected
                </Typography>
            ) : (
                <Typography
                    sx={{ flex: '1 1 100%' }}
                    variant="h6"
                    id="tableTitle"
                    component="div"
                >
                    Subscriptions
                </Typography>
            )}

            {numSelected > 0 && (
                <Box sx={{ display: 'flex', gap: 1 }}>
                    <Tooltip title="Renew Selected">
                        <Button
                            variant="outlined"
                            color="success"
                            startIcon={<RefreshIcon />}
                            onClick={onRenew}
                            size="small"
                        >
                            Renew
                        </Button>
                    </Tooltip>
                    <Tooltip title="Cancel Selected">
                        <Button
                            variant="outlined"
                            color="warning"
                            startIcon={<CancelIcon />}
                            onClick={onCancel}
                            size="small"
                        >
                            Cancel
                        </Button>
                    </Tooltip>
                    <Tooltip title="Delete Selected">
                        <Button
                            variant="outlined"
                            color="error"
                            startIcon={<DeleteIcon />}
                            onClick={onDelete}
                            size="small"
                        >
                            Delete
                        </Button>
                    </Tooltip>
                </Box>
            )}
        </Toolbar>
    );
};

// --- NEW: Debounced search component to prevent typing lag ---
const DebouncedSearchInput = ({ value, onChange, ...props }) => {
    const [localValue, setLocalValue] = useState(value || '');
    const onChangeRef = React.useRef(onChange);

    useEffect(() => {
        onChangeRef.current = onChange;
    }, [onChange]);

    useEffect(() => {
        const timeoutId = setTimeout(() => {
            if (localValue !== value) {
                onChangeRef.current(localValue);
            }
        }, 500);
        return () => clearTimeout(timeoutId);
    }, [localValue, value]);

    return (
        <TextField
            value={localValue}
            onChange={(e) => setLocalValue(e.target.value)}
            {...props}
        />
    );
};

const SubscriptionsView = ({
    customers,
    pagination,
    subscriptionPlans,
    refetchCustomers,
    setSnackbar,
    // --- PAGINATION STATE FROM PARENT ---
    currentPage,
    setCurrentPage,
    itemsPerPage,
    setItemsPerPage,
    searchQuery,
    setSearchQuery,
    customerSortBy,
    setCustomerSortBy,
    customerResellerId,
    setCustomerResellerId
}) => {
    const theme = useTheme();
    const { apiService } = useAppContext();
    const [showAddCustomerForm, setShowAddCustomerForm] = useState(false);
    const [newCustomer, setNewCustomer] = useState({
        name: '',
        phone: '',
        address: '',
        subscription_plan_id: '',
        reseller_id: '',
        discount: 0.0,
        subscription_start_date: new Date().toISOString().split('T')[0],
        additional_payment_amount: 0.0,
    });
    const [expandedCustomerId, setExpandedCustomerId] = useState(null);
    const [paymentsModalCustomer, setPaymentsModalCustomer] = useState(null);
    const [payments, setPayments] = useState([]);
    const [loadingPayments, setLoadingPayments] = useState(false);
    const [waReminderDialog, setWaReminderDialog] = useState({ open: false, customer: null });
    const [waReminderType, setWaReminderType] = useState('payment_reminder');

    const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingCustomer, setEditingCustomer] = useState(null);

    // --- NEW STATE ---
    const [viewMode, setViewMode] = useState('grid'); // 'grid' or 'list'
    const [statusFilter, setStatusFilter] = useState('active'); // 'active', 'canceled', 'all'
    const [resellers, setResellers] = useState([]);
    const [sectors, setSectors] = useState([]);

    useEffect(() => {
        apiService.fetchResellers().then(res => setResellers(res.data)).catch(err => console.error("Failed to load resellers", err));
        apiService.fetchSectors().then(res => setSectors(res.data)).catch(err => console.error("Failed to load sectors", err));
    }, []);
    const [selected, setSelected] = useState([]); // Array of customer IDs


    // Sync debouncedSearchQuery from parent's searchQuery (for backward compatibility)
    useEffect(() => {
        const timerId = setTimeout(() => {
            setDebouncedSearchQuery(searchQuery);
        }, 100); // Short delay just for UI consistency
        return () => clearTimeout(timerId);
    }, [searchQuery]);

    // --- NEW: Clear selection when changing view or customers list ---
    useEffect(() => {
        setSelected([]);
    }, [viewMode, customers, statusFilter]);


    const getStatusColor = (isActive) => (isActive ? '#10B981' : '#EF4444');
    const getPlanColor = (planName) => {
        const colors = { 'basic': '#4F46E5', 'premium': '#10B981', 'pro': '#F59E0B', 'enterprise': '#8B5CF6', 'default': '#6B7280' };
        return colors[planName?.toLowerCase()] || colors.default;
    };

    const fetchCustomerPayments = useCallback(async (customerId, customerObj = null) => {
        if (!customerObj && expandedCustomerId === customerId) {
            setExpandedCustomerId(null);
            setPayments([]);
            return;
        }
        if (customerObj) {
            setPaymentsModalCustomer(customerObj);
        } else {
            setExpandedCustomerId(customerId);
        }
        setLoadingPayments(true);
        try {
            const response = await apiService.fetchPayments(customerId);
            setPayments(response.data?.payments || response.payments || []);
        } catch (error) {
            console.error("Error fetching payments:", error);
            setSnackbar({ open: true, message: 'Failed to load payments.', severity: 'error' });
        } finally {
            setLoadingPayments(false);
        }
    }, [expandedCustomerId, apiService, setSnackbar]);

    const handleMarkPaid = useCallback(async (paymentId, currentAmount) => {
        const paymentAmountInput = prompt(`Enter amount received for Payment ID ${paymentId} (Outstanding: ${currentAmount.toFixed(2)}):`);
        const amountReceived = parseFloat(paymentAmountInput);

        if (isNaN(amountReceived) || amountReceived <= 0) {
            setSnackbar({ open: true, message: 'Please enter a valid positive amount.', severity: 'warning' });
            return;
        }

        const payload = {
            partial_payment: amountReceived < currentAmount,
            partial_amount: amountReceived
        };

        try {
            const response = await apiService.markPaymentAsPaid(paymentId, payload);
            setSnackbar({ open: true, message: response.data.message, severity: 'success' });
            if (expandedCustomerId) {
                fetchCustomerPayments(expandedCustomerId); // Refresh payments for the expanded customer
            }
            refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery); // Refetch customer list to update balance
        } catch (error) {
            console.error("Error marking payment paid:", error);
            setSnackbar({ open: true, message: 'Failed to mark payment as paid. ' + (error.response?.data?.error || error.message), severity: 'error' });
        }
    }, [apiService, setSnackbar, expandedCustomerId, fetchCustomerPayments, refetchCustomers, currentPage, itemsPerPage, debouncedSearchQuery]);

    const handleDeleteCustomer = useCallback(async (customerId) => {
        if (window.confirm('Are you sure you want to delete this customer? This action cannot be undone.')) {
            try {
                await apiService.deleteCustomer(customerId);
                setSnackbar({ open: true, message: 'Customer deleted successfully!', severity: 'success' });
                refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery);
            } catch (error) {
                console.error("Error deleting customer:", error);
                setSnackbar({ open: true, message: 'Failed to delete customer. ' + (error.response?.data?.error || error.message), severity: 'error' });
            }
        }
    }, [apiService, setSnackbar, refetchCustomers, currentPage, itemsPerPage, debouncedSearchQuery]);

    const handleSubscriptionAction = useCallback(async (action, customerId, confirmMessage) => {
        if (window.confirm(confirmMessage)) {
            try {
                await action(customerId);
                setSnackbar({ open: true, message: 'Action completed successfully!', severity: 'success' });
                refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery);
            } catch (error) {
                console.error(`Error with subscription action:`, error);
                setSnackbar({ open: true, message: `Failed to complete action. ${error.response?.data?.message || error.message}`, severity: 'error' });
            }
        }
    }, [apiService, setSnackbar, refetchCustomers, currentPage, itemsPerPage, debouncedSearchQuery]);
    const handleToggleWA = useCallback(async (customer) => {
        try {
            await apiService.updateCustomer(customer.id, {
                whatsapp_notifications_enabled: !customer.whatsapp_notifications_enabled
            });
            setSnackbar({ open: true, message: 'WhatsApp notifications preference updated', severity: 'success' });
            refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery);
        } catch (error) {
            console.error('Error toggling WA:', error);
            setSnackbar({ open: true, message: 'Failed to update preference', severity: 'error' });
        }
    }, [apiService, setSnackbar, refetchCustomers, currentPage, itemsPerPage, debouncedSearchQuery]);

    const handleSendWAReminder = useCallback((customerOrId) => {
        const customer = typeof customerOrId === 'object' ? customerOrId : customers.find(c => c.id === customerOrId);
        if (customer) {
            setWaReminderDialog({ open: true, customer });
            setWaReminderType('payment_reminder');
        }
    }, [customers]);

    const handleConfirmWAReminder = useCallback(async (templateType) => {
        if (!waReminderDialog.customer) return;
        const custId = waReminderDialog.customer.id;
        setWaReminderDialog({ open: false, customer: null });
        try {
            await apiService.sendWhatsappReminder(custId, templateType);
            setSnackbar({ open: true, message: `WhatsApp ${templateType.replace('_', ' ')} triggered!`, severity: 'success' });
        } catch (error) {
            console.error('Error sending WA reminder:', error);
            setSnackbar({ open: true, message: 'Failed to send WhatsApp reminder', severity: 'error' });
        }
    }, [waReminderDialog.customer, apiService, setSnackbar]);

    // --- NEW: Bulk Action Handlers ---
    const handleExportCSV = async () => {
        try {
            setSnackbar({ open: true, message: 'Preparing export...', severity: 'info' });
            // Fetch all customers matching current filters (per_page=9999)
            const response = await apiService.fetchCustomers(1, 9999, debouncedSearchQuery, customerSortBy, customerResellerId);
            const allCustomers = response.customers || [];
            
            if (allCustomers.length === 0) {
                setSnackbar({ open: true, message: 'No customers found to export.', severity: 'warning' });
                return;
            }

            // Prepare CSV content
            const headers = ['ID', 'Name', 'Phone', 'Address', 'Plan ID', 'Reseller ID', 'Discount', 'Balance', 'Start Date', 'Expiry Date', 'Is Active'];
            const csvRows = [];
            csvRows.push(headers.join(','));

            for (const customer of allCustomers) {
                const row = [
                    customer.id,
                    `"${(customer.name || '').replace(/"/g, '""')}"`,
                    `"${(customer.phone || '').replace(/"/g, '""')}"`,
                    `"${(customer.address || '').replace(/"/g, '""')}"`,
                    customer.subscription_plan_id || '',
                    customer.reseller_id || '',
                    customer.discount || 0,
                    customer.balance || 0,
                    customer.subscription_start_date || '',
                    customer.subscription_expiry_date || '',
                    customer.is_subscription_active ? 'Yes' : 'No'
                ];
                csvRows.push(row.join(','));
            }

            const csvString = csvRows.join('\n');
            const blob = new Blob([csvString], { type: 'text/csv' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.setAttribute('hidden', '');
            a.setAttribute('href', url);
            a.setAttribute('download', 'customers_export.csv');
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);

            setSnackbar({ open: true, message: 'Export successful!', severity: 'success' });
        } catch (error) {
            console.error('Error exporting CSV:', error);
            setSnackbar({ open: true, message: 'Failed to export CSV', severity: 'error' });
        }
    };

    const handleBulkAction = async (actionCallback, actionName, confirmMessage) => {
        if (window.confirm(confirmMessage)) {
            const results = await Promise.allSettled(
                selected.map(id => actionCallback(id))
            );

            const successCount = results.filter(r => r.status === 'fulfilled').length;
            const failCount = results.length - successCount;

            setSnackbar({
                open: true,
                message: `${actionName} successful for ${successCount} subscriptions. ${failCount > 0 ? `Failed for ${failCount}.` : ''}`,
                severity: failCount > 0 ? 'warning' : 'success'
            });

            setSelected([]); // Clear selection
            refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery); // Refresh data
        }
    };

    const handleBulkRenew = () => {
        // Note: The renew action doesn't require a confirmation in the single action, so we'll match that.
        // We'll wrap the apiService call in a function that fits the bulk action handler.
        const renewAction = (id) => apiService.renewSubscription(id);
        handleBulkAction(renewAction, 'Renew', `Renew ${selected.length} selected subscriptions? (Reseller customers will have their reseller charged, others will get new pending payments)`);
    };

    const handleBulkCancel = () => {
        const cancelAction = (id) => apiService.cancelSubscription(id);
        handleBulkAction(cancelAction, 'Cancel', `Cancel ${selected.length} selected subscriptions?`);
    };

    const handleBulkDelete = () => {
        // We need a wrapper function to delete without individual confirmation
        const deleteAction = (id) => apiService.deleteCustomer(id);
        handleBulkAction(deleteAction, 'Delete', `Are you sure you want to delete ${selected.length} selected customers? This action cannot be undone.`);
    };
    // --- End of NEW Bulk Action Handlers ---


    const handleUpdateCustomer = useCallback(async () => {
        if (!editingCustomer) return;

        try {
            const response = await apiService.updateCustomer(editingCustomer.id, {
                name: editingCustomer.name,
                phone: editingCustomer.phone,
                address: editingCustomer.address,
                sector: editingCustomer.sector,
                subscription_plan_id: editingCustomer.subscription_plan_id,
                discount: editingCustomer.discount,
                balance: editingCustomer.balance !== undefined ? editingCustomer.balance : 0,
                reseller_id: editingCustomer.reseller_id || ""
            });

            setSnackbar({
                open: true,
                message: response.data.message || 'Customer updated successfully!',
                severity: 'success'
            });

            setEditDialogOpen(false);
            setEditingCustomer(null);
            refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery);

        } catch (error) {
            console.error('Error updating customer:', error);
            setSnackbar({
                open: true,
                message: 'Failed to update customer. ' + (error.response?.data?.error || error.message),
                severity: 'error'
            });
        }
    }, [editingCustomer, apiService, setSnackbar, refetchCustomers, currentPage, itemsPerPage, debouncedSearchQuery]);

    const handleAddCustomer = useCallback(async () => {
        if (!newCustomer.name || !newCustomer.phone || !newCustomer.address || !newCustomer.subscription_plan_id) {
            setSnackbar({ open: true, message: 'Please fill out all required fields.', severity: 'warning' });
            return;
        }

        // OPTIMIZED: Show loading indicator during customer creation
        setSnackbar({ open: true, message: 'Creating customer and generating payment history...', severity: 'info' });

        try {
            await apiService.addCustomer(newCustomer);
            setSnackbar({ open: true, message: 'Customer added successfully!', severity: 'success' });
            setShowAddCustomerForm(false);
            setNewCustomer({ name: '', phone: '', address: '', sector: '', subscription_plan_id: '', discount: 0.0, subscription_start_date: new Date().toISOString().split('T')[0], additional_payment_amount: 0.0 });
            refetchCustomers(1, itemsPerPage, ''); // Go to first page after adding
        } catch (error) {
            console.error('Error adding customer:', error);
            setSnackbar({ open: true, message: 'Failed to add customer. ' + (error.response?.data?.error || error.message), severity: 'error' });
        }
    }, [newCustomer, apiService, setSnackbar, refetchCustomers, itemsPerPage]);

    const handlePageChange = (event, value) => {
        setCurrentPage(value);
    };

    // --- NEW: Selection Logic ---
    const handleSelectAllClick = (event) => {
        if (event.target.checked) {
            const newSelected = sortedCustomers.map((c) => c.id);
            setSelected(newSelected);
            return;
        }
        setSelected([]);
    };

    const handleSelectClick = (event, id) => {
        const selectedIndex = selected.indexOf(id);
        let newSelected = [];

        if (selectedIndex === -1) {
            newSelected = newSelected.concat(selected, id);
        } else if (selectedIndex === 0) {
            newSelected = newSelected.concat(selected.slice(1));
        } else if (selectedIndex === selected.length - 1) {
            newSelected = newSelected.concat(selected.slice(0, -1));
        } else if (selectedIndex > 0) {
            newSelected = newSelected.concat(
                selected.slice(0, selectedIndex),
                selected.slice(selectedIndex + 1),
            );
        }
        setSelected(newSelected);
    };

    const isSelected = (id) => selected.indexOf(id) !== -1;
    // --- End of NEW Selection Logic ---

    // Memoize search input handler to prevent lag
    const handleSearchChange = useCallback((value) => {
        setSearchQuery(value);
        setCurrentPage(1); // Reset to first page on new search
    }, [setSearchQuery, setCurrentPage]);

    const EmptyState = () => (
        <Fade in={true} timeout={800}>
            <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', py: 8, textAlign: 'center' }}>
                <Box sx={{ width: 120, height: 120, borderRadius: '50%', background: `linear-gradient(135deg, ${alpha(theme.palette.primary.main, 0.1)}, ${alpha(theme.palette.secondary.main, 0.1)})`, display: 'flex', alignItems: 'center', justifyContent: 'center', mb: 3 }}>
                    <PersonIcon sx={{ fontSize: 48, color: theme.palette.primary.main, opacity: 0.7 }} />
                </Box>
                <Typography variant="h6" sx={{ color: 'text.secondary', mb: 1 }}>No customers found</Typography>
                <Typography variant="body2" sx={{ color: 'text.disabled', mb: 3 }}>
                    {searchQuery || statusFilter !== 'all' ? "Try adjusting your filters or search query." : "Start by adding your first customer."}
                </Typography>
                <Button variant="contained" startIcon={<AddIcon />} onClick={() => setShowAddCustomerForm(true)} sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5 }}>
                    Add Customer
                </Button>
            </Box>
        </Fade>
    );

    // --- NEW: Memoized sorted customers list ---
    const sortedCustomers = React.useMemo(() => {
        let filtered = [...customers];
        if (statusFilter === 'active') {
            filtered = filtered.filter(c => c.is_subscription_active);
        } else if (statusFilter === 'canceled') {
            filtered = filtered.filter(c => !c.is_subscription_active);
        }
        return filtered.sort((a, b) => {
            // Sort by expiration date (nearest first)
            const dateA = new Date(a.subscription_expiry_date);
            const dateB = new Date(b.subscription_expiry_date);
            return dateA - dateB;
        });
    }, [customers, statusFilter]);

    // OPTIMIZED: Memoize expensive revenue calculation to prevent re-computation on every render
    const estimatedRevenue = React.useMemo(() => {
        return customers
            .filter(c => c.is_subscription_active)
            .reduce((sum, customer) => {
                const plan = customer.subscription_plan;
                return sum + (plan ? plan.price - customer.discount : 0);
            }, 0)
            .toFixed(2);
    }, [customers]);

    return (
        <Box sx={{ p: 3, background: 'linear-gradient(135deg, #f6f9fc 0%, #ffffff 100%)', minHeight: '100vh' }}>
            <Paper elevation={0} sx={{ p: 4, mb: 4, borderRadius: '24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', position: 'relative', overflow: 'hidden' }}>
                <Box sx={{ position: 'absolute', top: -50, right: -50, width: 200, height: 200, borderRadius: '50%', background: alpha('#ffffff', 0.1), filter: 'blur(1px)' }} />
                <Box sx={{ position: 'relative', zIndex: 1 }}>
                    <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', md: 'flex-start' }, gap: { xs: 2, md: 0 }, mb: 3 }}>
                        <Box>
                            <Typography variant="h4" sx={{ fontWeight: 700, mb: 1 }}>Subscriptions Management</Typography>
                            <Typography variant="body1" sx={{ opacity: 0.9 }}>Manage customer subscriptions and track payments</Typography>
                        </Box>
                        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, gap: 2 }}>
                            <Button variant="contained" startIcon={<DownloadIcon />} onClick={handleExportCSV} sx={{ backgroundColor: 'rgba(255, 255, 255, 0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255, 255, 255, 0.3)', color: 'white', borderRadius: '16px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5, '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' }, transition: 'all 0.3s ease' }}>
                                Export CSV
                            </Button>
                            <Button variant="contained" startIcon={<AddIcon />} onClick={() => setShowAddCustomerForm(!showAddCustomerForm)} sx={{ backgroundColor: 'rgba(255, 255, 255, 0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255, 255, 255, 0.3)', color: 'white', borderRadius: '16px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5, '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' }, transition: 'all 0.3s ease' }}>
                                {showAddCustomerForm ? 'Hide Form' : 'Add Customer'}
                            </Button>
                        </Box>
                    </Box>
                    <Box sx={{ display: 'flex', gap: 3, alignItems: 'center', flexWrap: 'wrap' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <GroupIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Total Customers</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>{pagination?.total || 0}</Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CheckCircleIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Active Subscriptions</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>{customers.filter(c => c.is_subscription_active).length}</Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <TrendingUpIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>Est. Monthly Revenue</Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                                    ${estimatedRevenue}
                                </Typography>
                            </Box>
                        </Box>
                    </Box>
                </Box>
            </Paper>

            <Collapse in={showAddCustomerForm}>
                <Paper sx={{ p: 4, borderRadius: '20px', background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)', border: `1px solid ${alpha(theme.palette.divider, 0.08)}`, mb: 4 }}>
                    <Typography variant="h6" sx={{ mb: 3, fontWeight: 700 }}>Add New Customer</Typography>
                    <Grid container spacing={3}>
                        <Grid item xs={12} md={6}><TextField fullWidth label="Name" value={newCustomer.name} onChange={(e) => setNewCustomer({ ...newCustomer, name: e.target.value })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth label="Phone" value={newCustomer.phone} onChange={(e) => setNewCustomer({ ...newCustomer, phone: e.target.value })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth label="Address" value={newCustomer.address} onChange={(e) => setNewCustomer({ ...newCustomer, address: e.target.value })} /></Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth select label="Sector (Optional)" value={newCustomer.sector || ''} onChange={(e) => setNewCustomer({ ...newCustomer, sector: e.target.value })}>
                                <MenuItem value="">None</MenuItem>
                                {sectors && sectors.map(s => <MenuItem key={s.id} value={s.name}>{s.name}</MenuItem>)}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth select label="Reseller (Optional)" value={newCustomer.reseller_id || ''} onChange={(e) => setNewCustomer({ ...newCustomer, reseller_id: e.target.value })}>
                                <MenuItem value="">None</MenuItem>
                                {resellers && resellers.map(r => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth select label="Subscription Plan" value={newCustomer.subscription_plan_id} onChange={(e) => setNewCustomer({ ...newCustomer, subscription_plan_id: e.target.value })}>
                                <MenuItem value="">Select Subscription Plan</MenuItem>
                                {subscriptionPlans.map(plan => (<MenuItem key={plan.id} value={plan.id}>{plan.name} - ${plan.price}</MenuItem>))}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount (Fixed Amount)" value={newCustomer.discount} onChange={(e) => setNewCustomer({ ...newCustomer, discount: parseFloat(e.target.value) || 0.0 })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="date" label="Subscription Start Date" value={newCustomer.subscription_start_date} onChange={(e) => setNewCustomer({ ...newCustomer, subscription_start_date: e.target.value })} InputLabelProps={{ shrink: true }} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Additional Payment Amount" value={newCustomer.additional_payment_amount} onChange={(e) => setNewCustomer({ ...newCustomer, additional_payment_amount: parseFloat(e.target.value) || 0.0 })} helperText="For one-time charges on creation" /></Grid>
                    </Grid>
                    <Box sx={{ display: 'flex', gap: 2, mt: 3 }}>
                        <Button variant="contained" onClick={handleAddCustomer} sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5 }}>Add Customer</Button>
                        <Button variant="outlined" onClick={() => setShowAddCustomerForm(false)} sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5 }}>Cancel</Button>
                    </Box>
                </Paper>
            </Collapse>

            <Paper sx={{ p: 2, mb: 3, borderRadius: '16px' }}>
                <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
                    <DebouncedSearchInput placeholder="Search by name, phone, or address..." value={searchQuery} onChange={handleSearchChange} InputProps={{ startAdornment: <SearchIcon sx={{ mr: 1, color: 'text.secondary' }} /> }} sx={{ flex: 1, minWidth: 250 }} />
                    <TextField select label="Status" value={statusFilter} onChange={(e) => { setStatusFilter(e.target.value); setCurrentPage(1); }} sx={{ minWidth: 150 }}>
                        <MenuItem value="active">Active</MenuItem>
                        <MenuItem value="canceled">Canceled</MenuItem>
                        <MenuItem value="all">All</MenuItem>
                    </TextField>
                    <TextField select label="Reseller" value={customerResellerId || ''} onChange={(e) => setCustomerResellerId(e.target.value)} sx={{ minWidth: 150 }}>
                        <MenuItem value="">All Resellers</MenuItem>
                        {resellers && resellers.map(r => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                    </TextField>
                    <TextField select label="Sort By" value={customerSortBy || 'expiry_date'} onChange={(e) => setCustomerSortBy(e.target.value)} sx={{ minWidth: 150 }}>
                        <MenuItem value="expiry_date">Expiry Date</MenuItem>
                        <MenuItem value="name">Name</MenuItem>
                        <MenuItem value="address">Address</MenuItem>
                    </TextField>
                    <TextField select label="Items per page" value={itemsPerPage} onChange={(e) => { setItemsPerPage(Number(e.target.value)); setCurrentPage(1); }} sx={{ minWidth: 120 }}>
                        <MenuItem value={10}>10</MenuItem><MenuItem value={25}>25</MenuItem><MenuItem value={50}>50</MenuItem><MenuItem value={100}>100</MenuItem><MenuItem value={1000}>all</MenuItem>
                    </TextField>
                    {/* --- NEW: View Mode Toggle --- */}
                    <ToggleButtonGroup
                        value={viewMode}
                        exclusive
                        onChange={(e, newView) => newView && setViewMode(newView)}
                        aria-label="view mode"
                    >
                        <ToggleButton value="grid" aria-label="grid view">
                            <ViewModuleIcon />
                        </ToggleButton>
                        <ToggleButton value="list" aria-label="list view">
                            <ViewListIcon />
                        </ToggleButton>
                    </ToggleButtonGroup>
                </Box>
            </Paper>

            {/* --- NEW: Conditional Rendering based on viewMode --- */}
            {customers.length === 0 ? (
                <EmptyState />
            ) : viewMode === 'grid' ? (
                // --- GRID VIEW (Original) ---
                <Grid container spacing={3}>
                    {sortedCustomers.map((customer, index) => {
                        const plan = customer.subscription_plan;
                        const isExpanded = expandedCustomerId === customer.id;
                        return (
                            <Grid item xs={12} md={6} lg={4} key={customer.id}>
                                <Slide in={true} direction="up" timeout={300 + index * 50}>
                                    <Card sx={{ position: 'relative', overflow: 'visible', transition: 'all 0.3s', '&:hover': { transform: 'translateY(-4px)', boxShadow: `0 12px 24px ${alpha(theme.palette.common.black, 0.15)}` }, borderRadius: '16px', border: `1px solid ${alpha(theme.palette.divider, 0.08)}`, mb: 2 }}>
                                        <Box sx={{ position: 'absolute', top: 0, left: 0, right: 0, height: 4, background: `linear-gradient(90deg, ${getStatusColor(customer.is_subscription_active)}, ${alpha(getStatusColor(customer.is_subscription_active), 0.7)})` }} />
                                        <CardContent sx={{ p: 3 }}>
                                            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                                                <Box sx={{ display: 'flex', gap: 2, flex: 1 }}>
                                                    <Avatar sx={{ width: 56, height: 56, background: `linear-gradient(135deg, ${getPlanColor(plan?.name)}, ${alpha(getPlanColor(plan?.name), 0.7)})`, fontSize: '1.5rem', fontWeight: 700 }}>{customer.name.charAt(0).toUpperCase()}</Avatar>
                                                    <Box sx={{ flex: 1 }}>
                                                        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>{customer.name}</Typography>
                                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}><PhoneIcon sx={{ fontSize: 14, color: 'text.secondary' }} /><Typography variant="body2" color="text.secondary">{customer.phone}</Typography></Box>
                                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}><LocationOnIcon sx={{ fontSize: 14, color: 'text.secondary' }} /><Typography variant="body2" color="text.secondary">{customer.address}</Typography>
                                                            {customer.sector && (
                                                                <>
                                                                    <Typography variant="body2" color="text.secondary" sx={{ mx: 0.5 }}>•</Typography>
                                                                    <Chip size="small" label={`Sector: ${customer.sector}`} variant="outlined" sx={{ height: 20, fontSize: '0.7rem' }} />
                                                                </>
                                                            )}
                                                        </Box>
                                                    </Box>
                                                </Box>
                                                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-end' }}>
                                                    <FormControlLabel 
                                                        control={<Switch size="small" checked={customer.whatsapp_notifications_enabled !== false} onChange={() => handleToggleWA(customer)} color="primary" />} 
                                                        label={<Typography variant="caption" sx={{ fontWeight: 600 }}>WA Alerts</Typography>}
                                                        labelPlacement="start"
                                                        sx={{ m: 0 }}
                                                    />
                                                    <Chip label={customer.is_subscription_active ? 'Active' : 'Canceled'} size="small" sx={{ backgroundColor: alpha(getStatusColor(customer.is_subscription_active), 0.1), color: getStatusColor(customer.is_subscription_active), fontWeight: 600, fontSize: '0.75rem', border: `1px solid ${alpha(getStatusColor(customer.is_subscription_active), 0.2)}` }} />
                                                    <Chip label={`Balance: $${customer.balance.toFixed(2)}`} size="small" sx={{ backgroundColor: alpha(customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, 0.1), color: customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, fontWeight: 600, fontSize: '0.75rem', border: `1px solid ${alpha(customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, 0.2)}` }} />
                                                </Box>
                                            </Box>
                                            <Divider sx={{ my: 2, opacity: 0.6 }} />
                                            <Grid container spacing={2} sx={{ mb: 2 }}>
                                                <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Plan</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>{plan?.name || 'N/A'}</Typography></Box></Grid>
                                                <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Price</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>${((plan?.price || 0) - customer.discount).toFixed(2)}</Typography></Box></Grid>
                                                <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Start Date</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>{new Date(customer.subscription_start_date).toLocaleDateString()}</Typography></Box></Grid>
                                                <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Expiry Date</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>{new Date(customer.subscription_expiry_date).toLocaleDateString()}</Typography></Box></Grid>
                                            </Grid>
                                            <Divider sx={{ my: 2, opacity: 0.6 }} />
                                            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                                                <Button size="small" variant="outlined" startIcon={isExpanded ? <VisibilityOffIcon /> : <VisibilityIcon />} onClick={() => fetchCustomerPayments(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>{isExpanded ? 'Hide' : 'Payments'}</Button>
                                                <Button size="small" variant="outlined" color="info" startIcon={<EditIcon />} onClick={() => { setEditingCustomer(customer); setEditDialogOpen(true); }} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Edit</Button>
                                                <Button size="small" variant="outlined" color="success" startIcon={<RefreshIcon />} onClick={() => handleSubscriptionAction(apiService.renewSubscription, customer.id, "Renew subscription? (Reseller customers will have their reseller charged, others will get a new pending payment)")} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Renew</Button>
                                                <Button size="small" variant="outlined" color="primary" startIcon={<ChatIcon />} onClick={() => handleSendWAReminder(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>WA Reminder</Button>
                                                {customer.is_subscription_active ? (
                                                    <Button size="small" variant="outlined" color="warning" startIcon={<CancelIcon />} onClick={() => handleSubscriptionAction(apiService.cancelSubscription, customer.id, "Cancel subscription?")} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Cancel</Button>
                                                ) : (
                                                    <Button size="small" variant="outlined" color="success" startIcon={<PlayArrowIcon />} onClick={() => handleSubscriptionAction(apiService.activateSubscription, customer.id, "Activate subscription?")} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Activate</Button>
                                                )}
                                                <Button size="small" variant="outlined" color="error" startIcon={<DeleteIcon />} onClick={() => handleDeleteCustomer(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Delete</Button>
                                            </Box>
                                            <Collapse in={isExpanded}>
                                                <Box sx={{ mt: 3, p: 2, backgroundColor: alpha(theme.palette.primary.main, 0.02), borderRadius: '12px' }}>
                                                    <Typography variant="h6" sx={{ mb: 2, fontWeight: 700 }}>Payments</Typography>
                                                    {loadingPayments ? <CircularProgress size={24} /> : (
                                                        <TableContainer>
                                                            <Table size="small">
                                                                <TableHead><TableRow><TableCell sx={{ fontWeight: 700 }}>Date</TableCell><TableCell sx={{ fontWeight: 700 }}>Amount</TableCell><TableCell sx={{ fontWeight: 700 }}>Status</TableCell><TableCell sx={{ fontWeight: 700 }}>Actions</TableCell></TableRow></TableHead>
                                                                <TableBody>
                                                                    {payments.length > 0 ? payments.map(p => (
                                                                        <TableRow key={p.id}>
                                                                            <TableCell>{new Date(p.date).toLocaleDateString()}</TableCell>
                                                                            <TableCell sx={{ fontWeight: 600 }}>${p.amount.toFixed(2)}</TableCell>
                                                                            <TableCell><Chip label={p.paid ? 'Paid' : 'Unpaid'} size="small" color={p.paid ? 'success' : 'error'} variant="outlined" /></TableCell>
                                                                            <TableCell>{!p.paid && <Button size="small" variant="contained" color="success" onClick={() => handleMarkPaid(p.id, p.amount)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Mark Paid</Button>}</TableCell>
                                                                        </TableRow>
                                                                    )) : <TableRow><TableCell colSpan={4} sx={{ textAlign: 'center', py: 3 }}><Typography variant="body2" color="text.secondary">No payments found</Typography></TableCell></TableRow>}
                                                                </TableBody>
                                                            </Table>
                                                        </TableContainer>
                                                    )}
                                                </Box>
                                            </Collapse>
                                        </CardContent>
                                    </Card>
                                </Slide>
                            </Grid>
                        );
                    })}
                </Grid>
            ) : (
                // --- LIST VIEW (New) ---
                <Paper sx={{ width: '100%', mb: 2, borderRadius: '16px', overflow: 'hidden' }}>
                    <EnhancedTableToolbar
                        numSelected={selected.length}
                        onRenew={handleBulkRenew}
                        onCancel={handleBulkCancel}
                        onDelete={handleBulkDelete}
                    />
                    <TableContainer>
                        <Table sx={{ minWidth: 750 }} aria-labelledby="tableTitle">
                            <TableHead sx={{ backgroundColor: alpha(theme.palette.primary.main, 0.05) }}>
                                <TableRow>
                                    <TableCell padding="checkbox">
                                        <Checkbox
                                            color="primary"
                                            indeterminate={selected.length > 0 && selected.length < sortedCustomers.length}
                                            checked={sortedCustomers.length > 0 && selected.length === sortedCustomers.length}
                                            onChange={handleSelectAllClick}
                                            inputProps={{ 'aria-label': 'select all customers' }}
                                        />
                                    </TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Customer</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Contact</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Plan</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>WA Alerts</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Balance</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Expiry Date</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {sortedCustomers.map((customer, index) => {
                                    const isItemSelected = isSelected(customer.id);
                                    const labelId = `enhanced-table-checkbox-${index}`;
                                    const plan = customer.subscription_plan;

                                    return (
                                        <TableRow
                                            hover
                                            onClick={(event) => handleSelectClick(event, customer.id)}
                                            role="checkbox"
                                            aria-checked={isItemSelected}
                                            tabIndex={-1}
                                            key={customer.id}
                                            selected={isItemSelected}
                                            sx={{ cursor: 'pointer', '&.Mui-selected': { backgroundColor: alpha(theme.palette.primary.main, 0.08) } }}
                                        >
                                            <TableCell padding="checkbox">
                                                <Checkbox
                                                    color="primary"
                                                    checked={isItemSelected}
                                                    inputProps={{ 'aria-labelledby': labelId }}
                                                />
                                            </TableCell>
                                            <TableCell component="th" id={labelId} scope="row" padding="none">
                                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 1 }}>
                                                    <Avatar sx={{ background: `linear-gradient(135deg, ${getPlanColor(plan?.name)}, ${alpha(getPlanColor(plan?.name), 0.7)})` }}>
                                                        {customer.name.charAt(0).toUpperCase()}
                                                    </Avatar>
                                                    <Box>
                                                        <Typography variant="body1" sx={{ fontWeight: 600 }}>{customer.name}</Typography>
                                                        <Typography variant="body2" color="text.secondary">{customer.address}</Typography>
                                                        {customer.sector && <Typography variant="caption" color="text.secondary">Sector: {customer.sector}</Typography>}
                                                    </Box>
                                                </Box>
                                            </TableCell>
                                            <TableCell>{customer.phone}</TableCell>
                                            <TableCell>
                                                <Typography variant="body2" sx={{ fontWeight: 500 }}>{plan?.name || 'N/A'}</Typography>
                                                <Typography variant="caption" color="text.secondary">${((plan?.price || 0) - customer.discount).toFixed(2)}</Typography>
                                            </TableCell>
                                            <TableCell>
                                                <Chip
                                                    label={customer.is_subscription_active ? 'Active' : 'Canceled'}
                                                    size="small"
                                                    sx={{
                                                        backgroundColor: alpha(getStatusColor(customer.is_subscription_active), 0.1),
                                                        color: getStatusColor(customer.is_subscription_active),
                                                        fontWeight: 600,
                                                    }}
                                                />
                                            </TableCell>
                                            <TableCell onClick={(e) => e.stopPropagation()}>
                                                <Switch size="small" checked={customer.whatsapp_notifications_enabled !== false} onChange={() => handleToggleWA(customer)} color="primary" />
                                            </TableCell>
                                            <TableCell>
                                                <Chip
                                                    label={`$${customer.balance.toFixed(2)}`}
                                                    size="small"
                                                    sx={{
                                                        backgroundColor: alpha(customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, 0.1),
                                                        color: customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main,
                                                        fontWeight: 600,
                                                    }}
                                                />
                                            </TableCell>
                                            <TableCell>{new Date(customer.subscription_expiry_date).toLocaleDateString()}</TableCell>
                                            <TableCell onClick={(e) => e.stopPropagation()} sx={{ whiteSpace: 'nowrap' }}>
                                                {/* Stop propagation so clicking buttons doesn't select the row */}
                                                <Tooltip title="Payments History">
                                                    <IconButton size="small" color="secondary" onClick={() => fetchCustomerPayments(customer.id, customer)}>
                                                        <ReceiptIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                                <Tooltip title="Edit">
                                                    <IconButton size="small" color="info" onClick={() => { setEditingCustomer(customer); setEditDialogOpen(true); }}>
                                                        <EditIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                                <Tooltip title="Renew">
                                                    <IconButton size="small" color="success" onClick={() => handleSubscriptionAction(apiService.renewSubscription, customer.id, "Renew subscription? (Reseller customers will have their reseller charged, others will get a new pending payment)")}>
                                                        <RefreshIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                                <Tooltip title="WA Reminder">
                                                    <IconButton size="small" color="primary" onClick={() => handleSendWAReminder(customer.id)}>
                                                        <ChatIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                                {customer.is_subscription_active ? (
                                                    <Tooltip title="Cancel">
                                                        <IconButton size="small" color="warning" onClick={() => handleSubscriptionAction(apiService.cancelSubscription, customer.id, "Cancel subscription?")}>
                                                            <CancelIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                ) : (
                                                    <Tooltip title="Activate">
                                                        <IconButton size="small" color="success" onClick={() => handleSubscriptionAction(apiService.activateSubscription, customer.id, "Activate subscription?")}>
                                                            <PlayArrowIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
                                                <Tooltip title="Delete">
                                                    <IconButton size="small" color="error" onClick={() => handleDeleteCustomer(customer.id)}>
                                                        <DeleteIcon fontSize="small" />
                                                    </IconButton>
                                                </Tooltip>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </Paper>
            )}


            <Box sx={{ display: 'flex', justifyContent: 'center', mt: 4 }}>
                <Pagination count={pagination?.pages || 1} page={currentPage} onChange={handlePageChange} color="primary" />
            </Box>

            <Dialog open={editDialogOpen} onClose={() => setEditDialogOpen(false)} maxWidth="md" fullWidth>
                <DialogTitle>Edit Customer</DialogTitle>
                <DialogContent>
                    <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={12} md={6}><TextField fullWidth label="Name" value={editingCustomer?.name || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, name: e.target.value })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth label="Phone" value={editingCustomer?.phone || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, phone: e.target.value })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth label="Address" value={editingCustomer?.address || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, address: e.target.value })} /></Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth select label="Sector (Optional)" value={editingCustomer?.sector || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, sector: e.target.value })}>
                                <MenuItem value="">None</MenuItem>
                                {sectors && sectors.map(s => <MenuItem key={s.id} value={s.name}>{s.name}</MenuItem>)}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth select label="Reseller (Optional)" value={editingCustomer?.reseller_id || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, reseller_id: e.target.value })}>
                                <MenuItem value="">None</MenuItem>
                                {resellers && resellers.map(r => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth select label="Subscription Plan" value={editingCustomer?.subscription_plan_id || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, subscription_plan_id: e.target.value })}>
                                {subscriptionPlans.map(plan => (<MenuItem key={plan.id} value={plan.id}>{plan.name} - ${plan.price}</MenuItem>))}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount ($)" value={editingCustomer?.discount || 0} onChange={(e) => setEditingCustomer({ ...editingCustomer, discount: parseFloat(e.target.value) || 0 })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Account Balance ($)" value={editingCustomer?.balance !== undefined ? editingCustomer.balance : 0} helperText="Negative value = Customer owes money. 0 = Paid." onChange={(e) => setEditingCustomer({ ...editingCustomer, balance: parseFloat(e.target.value) || 0 })} /></Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleUpdateCustomer}>Save Changes</Button>
                </DialogActions>
            </Dialog>

            <Dialog open={Boolean(paymentsModalCustomer)} onClose={() => { setPaymentsModalCustomer(null); setPayments([]); }} maxWidth="md" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Payments History - {paymentsModalCustomer?.name}</DialogTitle>
                <DialogContent>
                    {loadingPayments ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', my: 4 }}>
                            <CircularProgress size={32} />
                        </Box>
                    ) : (
                        <TableContainer sx={{ mt: 1 }}>
                            <Table size="small">
                                <TableHead>
                                    <TableRow sx={{ backgroundColor: alpha(theme.palette.primary.main, 0.05) }}>
                                        <TableCell sx={{ fontWeight: 700 }}>Date</TableCell>
                                        <TableCell sx={{ fontWeight: 700 }}>Amount</TableCell>
                                        <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                                        <TableCell sx={{ fontWeight: 700 }}>Actions</TableCell>
                                    </TableRow>
                                </TableHead>
                                <TableBody>
                                    {payments.length > 0 ? payments.map(p => (
                                        <TableRow key={p.id}>
                                            <TableCell>{new Date(p.date).toLocaleDateString()}</TableCell>
                                            <TableCell sx={{ fontWeight: 600 }}>${p.amount.toFixed(2)}</TableCell>
                                            <TableCell><Chip label={p.paid ? 'Paid' : 'Unpaid'} size="small" color={p.paid ? 'success' : 'error'} variant="outlined" /></TableCell>
                                            <TableCell>{!p.paid && <Button size="small" variant="contained" color="success" onClick={() => handleMarkPaid(p.id, p.amount)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Mark Paid</Button>}</TableCell>
                                        </TableRow>
                                    )) : (
                                        <TableRow><TableCell colSpan={4} sx={{ textAlign: 'center', py: 4 }}><Typography variant="body2" color="text.secondary">No payment history found for this customer.</Typography></TableCell></TableRow>
                                    )}
                                </TableBody>
                            </Table>
                        </TableContainer>
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => { setPaymentsModalCustomer(null); setPayments([]); }}>Close</Button>
                </DialogActions>
            </Dialog>

            {/* WhatsApp Reminder Template Selection Dialog */}
            <Dialog open={waReminderDialog.open} onClose={() => setWaReminderDialog({ open: false, customer: null })} maxWidth="sm" fullWidth>
                <DialogTitle sx={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 1 }}>
                    <ChatIcon sx={{ color: '#25D366' }} /> Select WhatsApp Message Template
                </DialogTitle>
                <DialogContent>
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                        Choose which WhatsApp message template to send to <b>{waReminderDialog.customer?.name}</b>:
                    </Typography>

                    <Grid container spacing={2}>
                        <Grid item xs={12}>
                            <Paper
                                variant="outlined"
                                onClick={() => setWaReminderType('payment_reminder')}
                                sx={{
                                    p: 2.5,
                                    borderRadius: '16px',
                                    cursor: 'pointer',
                                    border: '2px solid',
                                    borderColor: waReminderType === 'payment_reminder' ? '#25D366' : alpha(theme.palette.divider, 0.1),
                                    bgcolor: waReminderType === 'payment_reminder' ? alpha('#25D366', 0.05) : 'transparent',
                                    transition: 'all 0.2s ease',
                                    '&:hover': {
                                        borderColor: '#25D366',
                                        bgcolor: alpha('#25D366', 0.02)
                                    }
                                }}
                            >
                                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                                    <Typography variant="subtitle1" sx={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 1 }}>
                                        💬 Payment Reminder Template
                                    </Typography>
                                    <Chip label="payment_reminder" size="small" sx={{ bgcolor: alpha('#25D366', 0.1), color: '#25D366', fontWeight: 600 }} />
                                </Box>
                                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                                    Standard reminder for pending or due payments.
                                </Typography>
                                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                                    <Chip label="1- Customer Name" size="small" variant="outlined" sx={{ fontSize: '0.75rem' }} />
                                    <Chip label="2- Balance Due" size="small" variant="outlined" sx={{ fontSize: '0.75rem' }} />
                                    <Chip label="3- Expiry Date" size="small" variant="outlined" sx={{ fontSize: '0.75rem' }} />
                                </Box>
                            </Paper>
                        </Grid>

                        <Grid item xs={12}>
                            <Paper
                                variant="outlined"
                                onClick={() => setWaReminderType('current_balance')}
                                sx={{
                                    p: 2.5,
                                    borderRadius: '16px',
                                    cursor: 'pointer',
                                    border: '2px solid',
                                    borderColor: waReminderType === 'current_balance' ? '#25D366' : alpha(theme.palette.divider, 0.1),
                                    bgcolor: waReminderType === 'current_balance' ? alpha('#25D366', 0.05) : 'transparent',
                                    transition: 'all 0.2s ease',
                                    '&:hover': {
                                        borderColor: '#25D366',
                                        bgcolor: alpha('#25D366', 0.02)
                                    }
                                }}
                            >
                                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                                    <Typography variant="subtitle1" sx={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 1 }}>
                                        📊 Current Balance Template
                                    </Typography>
                                    <Chip label="current_balance" size="small" sx={{ bgcolor: alpha('#25D366', 0.1), color: '#25D366', fontWeight: 600 }} />
                                </Box>
                                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                                    Send account status summary with current balance and subscription expiration date.
                                </Typography>
                                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                                    <Chip label="1- Customer Name" size="small" variant="outlined" sx={{ fontSize: '0.75rem' }} />
                                    <Chip label="2- Current Balance" size="small" variant="outlined" sx={{ fontSize: '0.75rem' }} />
                                    <Chip label="3- Expiry Date" size="small" variant="outlined" sx={{ fontSize: '0.75rem' }} />
                                </Box>
                            </Paper>
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions sx={{ p: 2.5, pt: 1 }}>
                    <Button onClick={() => setWaReminderDialog({ open: false, customer: null })} sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
                        Cancel
                    </Button>
                    <Button
                        variant="contained"
                        onClick={() => handleConfirmWAReminder(waReminderType)}
                        startIcon={<ChatIcon />}
                        sx={{
                            bgcolor: '#25D366',
                            color: '#fff',
                            borderRadius: '10px',
                            textTransform: 'none',
                            fontWeight: 700,
                            px: 3,
                            '&:hover': { bgcolor: '#1ebe5d' }
                        }}
                    >
                        Send WhatsApp Message
                    </Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default SubscriptionsView;
