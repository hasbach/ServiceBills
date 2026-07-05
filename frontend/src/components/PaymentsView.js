import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
    Box,
    Typography,
    Paper,
    Button,
    CircularProgress,
    Dialog,
    Card,
    CardContent,
    Chip,
    IconButton,
    Fade,
    Grid,
    Divider,
    alpha,
    useTheme,
    TextField,
    MenuItem,
    DialogTitle,
    DialogContent,
    DialogActions,
    FormControlLabel,
    Checkbox,
    Avatar,
    Modal,
    InputAdornment,
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Toolbar,
    Tooltip,
    ToggleButton,
    ToggleButtonGroup,
    Autocomplete
} from '@mui/material';
import {
    Add as AddIcon,
    Payment as PaymentIcon,
    CalendarToday as CalendarIcon,
    Money as MoneyIcon,
    Print as PrintIcon,
    Delete as DeleteIcon,
    CheckCircle as CheckCircleIcon,
    TrendingUp as TrendingUpIcon,
    Receipt as ReceiptIcon,
    FilterList as FilterListIcon,
    Search as SearchIcon,
    Close as CloseIcon,
    ScheduleSend as ScheduleSendIcon,
    ViewList as ViewListIcon,
    ViewModule as ViewModuleIcon,
    WhatsApp as WhatsAppIcon,
    LocationOn as LocationIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';


// Revenue helpers — kept outside component so they are never recreated
const getTotalRevenue = (payments) => {
    if (!Array.isArray(payments)) return 0;
    return payments.reduce((sum, p) => p.paid ? sum + (parseFloat(p.amount) || 0) : sum, 0);
};

const getCurrentMonthRevenue = (payments) => {
    if (!Array.isArray(payments)) return 0;
    const now = new Date();
    const y = now.getFullYear();
    const m = now.getMonth();
    return payments.reduce((sum, p) => {
        if (!p.paid) return sum;
        const d = new Date(p.date);
        return (d.getFullYear() === y && d.getMonth() === m) ? sum + (Number(p.amount) || 0) : sum;
    }, 0);
};



// Modal style for consistent popups
const modalStyle = {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
    width: 400,
    bgcolor: 'background.paper',
    border: '2px solid #000',
    boxShadow: 24,
    p: 4,
    borderRadius: '8px',
};

// --- NEW: Debounced search component to prevent typing lag ---
const DebouncedSearchInput = ({ value, onChange, ...props }) => {
    const [localValue, setLocalValue] = useState(value || '');
    const onChangeRef = useRef(onChange);

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

// --- NEW: Component for the specific printable receipt format ---
const PrintableReceipt = React.forwardRef(({ receiptData }, ref) => {
    if (!receiptData) return null;

    const arabicMonths = [
        'كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران',
        'تموز', 'آب', 'أيلول', 'تشرين الأول', 'تشرين الثاني', 'كانون الأول'
    ];
    const date = new Date(receiptData.payment_date);
    const month = date.getMonth();
    const year = date.getFullYear();
    const arabicMonthYear = `${arabicMonths[month]} ${year}`;

    const currentBalance = (parseFloat(receiptData.customer_new_balance) || 0).toFixed(2);
    const previousBalance = ((parseFloat(receiptData.customer_new_balance) || 0) + (parseFloat(receiptData.amount_paid_now) || 0)).toFixed(2);



    return (
        <Box
            ref={ref}
            id="receipt-to-print"
            sx={{
                width: '21cm',
                height: '7.5cm',
                display: 'flex',
                fontFamily: 'Arial, sans-serif',
                margin: '0 auto',
                backgroundColor: 'white',
                paddingTop: '40px',
                boxSizing: 'border-box',
                border: 'none',
                direction: 'rtl',
                color: '#000' // Ensure text is black
            }}
        >
            {/* Main Part - 14.1cm (Right Side) */}
            <Box
                sx={{
                    width: '14.1cm',
                    padding: '10px 15px 10px 15px',
                    boxSizing: 'border-box',
                    display: 'flex',
                    flexDirection: 'row-reverse',
                    alignItems: 'flex-start',
                    justifyContent: 'flex-start',
                }}
            >
                <Box
                    sx={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '2px',
                        fontSize: '18px',
                        flexGrow: 1,
                        paddingRight: '15px',
                        width: '100%'
                    }}
                >
                    <Typography component="span" sx={{ display: 'block' }}>
                        الإسم: {receiptData.customer_name}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block' }}>
                        العنوان: {receiptData.customer_address}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block' }}>
                        الهاتف: {receiptData.customer_phone}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block' }}>
                        تاريخ الإيصال: {receiptData.payment_date}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block' }}>
                        المبلغ الشهري المستحق: ${(parseFloat(receiptData.subscription_plan_details?.price) || 0).toFixed(2)} / الخدمة: {receiptData.subscription_plan_details?.name || 'انترنت'}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block' }}>
                        عن شهر: {arabicMonthYear}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block', fontWeight: 'bold' }}>
                        الرصيد الحالي: ${currentBalance} / الرصيد السابق: ${previousBalance}
                    </Typography>
                </Box>
            </Box>

            {/* Mini Part - 6.9cm (Left Side) */}
            <Box
                sx={{
                    width: '6.9cm',
                    padding: '10px 15px 10px 15px',
                    boxSizing: 'border-box',
                    borderRight: '1px dashed #000', // Add a separator line
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'flex-start',
                    fontSize: '14px',
                }}
            >
                <Box
                    sx={{
                        flexGrow: 1,
                        fontSize: '16px',
                        display: 'flex',
                        flexDirection: 'column',
                        gap: '2px',
                        width: '100%'
                    }}
                >
                    <Typography component="span" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        الإسم: {receiptData.customer_name}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        العنوان: {receiptData.customer_address}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        الهاتف: {receiptData.customer_phone}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        المبلغ الشهري: ${(parseFloat(receiptData.subscription_plan_details?.price) || 0).toFixed(2)}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        الرصيد الحالي: ${currentBalance}
                    </Typography>
                    <Typography component="span" sx={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        الرصيد السابق: ${previousBalance}
                    </Typography>
                </Box>
            </Box>
        </Box>
    );
});


// ─────────────────────────────────────────────────────────────────────────────
// _PaymentCard — module-level so React.memo actually memoizes between renders.
// All event-handler props are passed in via cardHandlers (stable useMemo object).
// ─────────────────────────────────────────────────────────────────────────────
const PaymentCardItem = React.memo(({
    payment,
    getStatusColor, getPaymentTypeColor,
    openMarkPaidDialog, handlePrepareReceipt, handleDeletePayment,
    buildWhatsAppLink, waSettings, userRoles,
}) => {
    const isCollector = userRoles.includes('collector') || userRoles.includes('admin') || userRoles.includes('finance');
    const isAdminOrFinance = userRoles.includes('admin') || userRoles.includes('finance');

    const theme = useTheme();
    return (
        <Fade in timeout={300}>
            <Card
                sx={{
                    position: 'relative',
                    overflow: 'hidden',
                    transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
                    cursor: 'pointer',
                    '&:hover': {
                        transform: 'translateY(-4px)',
                        boxShadow: `0 12px 24px ${alpha(theme.palette.common.black, 0.15)}`,
                        '& .payment-actions': { opacity: 1, transform: 'translateX(0)' }
                    },
                    borderRadius: '16px',
                    background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
                    border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                    mb: 2
                }}
            >
                <Box sx={{ position: 'absolute', top: 0, left: 0, right: 0, height: 4, background: `linear-gradient(90deg, ${getStatusColor(payment.paid)}, ${alpha(getStatusColor(payment.paid), 0.7)})` }} />
                <CardContent sx={{ p: 3 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
                        <Box sx={{ display: 'flex', gap: 2, flex: 1 }}>
                            <Avatar sx={{ width: 56, height: 56, background: `linear-gradient(135deg, ${getPaymentTypeColor(payment.pre_payment)}, ${alpha(getPaymentTypeColor(payment.pre_payment), 0.7)})`, fontSize: '1.25rem', fontWeight: 700 }}>
                                {payment.customer_name ? payment.customer_name.charAt(0).toUpperCase() : 'N'}
                            </Avatar>
                            <Box sx={{ flex: 1 }}>
                                <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.2 }}>{payment.customer_name || 'Unknown Customer'}</Typography>
                                {payment.customer_address && (
                                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.5 }}>
                                        <LocationIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                                        <Typography variant="caption" color="text.secondary">{payment.customer_address}</Typography>
                                    </Box>
                                )}
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                                    <CalendarIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                                    <Typography variant="body2" color="text.secondary">Billed: {new Date(payment.date).toLocaleDateString()}</Typography>
                                </Box>
                                {payment.paid_at && (
                                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                                        <CheckCircleIcon sx={{ fontSize: 14, color: theme.palette.success.main }} />
                                        <Typography variant="body2" color="text.secondary">Paid: {new Date(payment.paid_at).toLocaleDateString()}</Typography>
                                    </Box>
                                )}
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                                    <MoneyIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                                    <Typography variant="h6" sx={{ fontWeight: 700, color: getStatusColor(payment.paid) }}>
                                        ${(parseFloat(payment.amount) || 0).toFixed(2)}
                                    </Typography>
                                </Box>
                            </Box>
                        </Box>
                        <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 1 }}>
                            <Chip label={payment.paid ? 'Paid' : (payment.collected ? (payment.collected_amount ? `Collected ($${payment.collected_amount.toFixed(2)})` : 'Collected') : 'Unpaid')} size="small"
                                sx={{ backgroundColor: alpha(getStatusColor(payment.paid), 0.1), color: getStatusColor(payment.paid), fontWeight: 600, fontSize: '0.75rem', border: `1px solid ${alpha(getStatusColor(payment.paid), 0.2)}` }} />
                            {payment.pre_payment && (
                                <Chip label="Pre-Payment" size="small"
                                    sx={{ backgroundColor: alpha(theme.palette.secondary.main, 0.1), color: theme.palette.secondary.main, fontWeight: 600, fontSize: '0.75rem', border: `1px solid ${alpha(theme.palette.secondary.main, 0.2)}` }} />
                            )}
                            {payment.reason && (
                                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5, fontStyle: 'italic' }}>
                                    Reason: {payment.reason}
                                </Typography>
                            )}
                            {payment.collected && !payment.paid && (
                                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                                    by {payment.collected_by} {payment.collected_amount ? `($${payment.collected_amount.toFixed(2)})` : ''}
                                </Typography>
                            )}
                            {payment.paid && payment.received_by && (
                                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                                    rcvd by {payment.received_by}
                                </Typography>
                            )}
                        </Box>
                    </Box>
                    <Divider sx={{ my: 2, opacity: 0.6 }} />
                    <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }} onClick={(e) => e.stopPropagation()}>
                        {!payment.paid && (
                            <>
                                {((!payment.collected && isCollector) || (payment.collected && isAdminOrFinance)) && (
                                    <Button size="small" variant="outlined" startIcon={<CheckCircleIcon />} onClick={() => openMarkPaidDialog(payment)} sx={{ borderColor: alpha('#10B981', 0.3), color: '#10B981', '&:hover': { borderColor: '#10B981', backgroundColor: alpha('#10B981', 0.05) } }}>
                                        {payment.collected ? 'Confirm Receipt' : 'Collect'}
                                    </Button>
                                )}
                                {isAdminOrFinance && (
                                    <Button size="small" variant="outlined" startIcon={<PrintIcon />} onClick={() => handlePrepareReceipt(payment.id)} sx={{ borderColor: alpha(theme.palette.primary.main, 0.3), '&:hover': { borderColor: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, 0.05) } }}>
                                        Print Receipt
                                    </Button>
                                )}
                            </>
                        )}
                        {payment.paid && isAdminOrFinance && (
                            <Button size="small" variant="outlined" startIcon={<PrintIcon />} onClick={() => handlePrepareReceipt(payment.id)} sx={{ borderColor: alpha(theme.palette.primary.main, 0.3), '&:hover': { borderColor: theme.palette.primary.main, backgroundColor: alpha(theme.palette.primary.main, 0.05) } }}>
                                Print Receipt
                            </Button>
                        )}
                        {isAdminOrFinance && waSettings.enabled && waSettings.mode === 'deeplink' && payment.paid && (() => {
                            const link = buildWhatsAppLink(payment);
                            return link ? (
                                <Button size="small" variant="outlined" startIcon={<WhatsAppIcon />}
                                    component="a" href={link} target="_blank" rel="noopener noreferrer"
                                    sx={{ borderColor: alpha('#25D366', 0.4), color: '#25D366', '&:hover': { borderColor: '#25D366', backgroundColor: alpha('#25D366', 0.06) } }}>
                                    WhatsApp
                                </Button>
                            ) : null;
                        })()}
                        {isAdminOrFinance && (
                            <Button size="small" variant="outlined" startIcon={<DeleteIcon />} onClick={() => handleDeletePayment && handleDeletePayment(payment.id)} sx={{ borderColor: alpha('#EF4444', 0.3), color: '#EF4444', '&:hover': { borderColor: '#EF4444', backgroundColor: alpha('#EF4444', 0.05) } }}>
                                Delete
                            </Button>
                        )}
                    </Box>
                </CardContent>
            </Card>
        </Fade>
    );
});


const PaymentsView = () => {
    const { user, apiService, setSnackbar } = useAppContext();
    const userRoles = user?.role ? user.role.split(',').map(r => r.trim().toLowerCase()) : [];

    const theme = useTheme();
    const [payments, setPayments] = useState([]);
    const [customers, setCustomers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [currentTab, setCurrentTab] = useState(0);
    const [viewMode, setViewMode] = useState('grid'); // 'grid' or 'list'
    const [selected, setSelected] = useState([]); // selected payment IDs for bulk actions
    // Mark-as-Paid dialog
    const [markPaidDialog, setMarkPaidDialog] = useState({ open: false, paymentId: null, outstanding: 0, customerName: '' });
    const [markPaidAmount, setMarkPaidAmount] = useState('');
    const [waSettings, setWaSettings] = useState({ enabled: false, mode: 'deeplink', deeplink_msg_payment: 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!' });

    // Fetch WhatsApp settings once
    useEffect(() => {
        apiService.fetchWhatsAppSettings().then(res => {
            if (res.data?.settings) setWaSettings(res.data.settings);
        }).catch(() => {});
    }, [apiService]);

    // Build wa.me deep-link for a paid payment
    const buildWhatsAppLink = (payment) => {
        const phone = (payment.customer_phone || '').replace(/\D/g, '');
        if (!phone) return null;
        const msg = (waSettings.deeplink_msg_payment || '')
            .replace('{customer_name}', payment.customer_name || '')
            .replace('{amount}', parseFloat(payment.amount || 0).toFixed(2));
        return `https://wa.me/${phone}?text=${encodeURIComponent(msg)}`;
    };

    const [filters, setFilters] = useState({
        customer_id: '',
        status: 'unpaid',
        start_date: '',
        end_date: '',
        search_query: '',
        collected_by: '',
        collected_date: '',
        sort_by: 'billed_date',
        sort_desc: 'true'
    });

    const [collectors, setCollectors] = useState([]);
    useEffect(() => {
        apiService.fetchUsers().then(res => {
            const users = res.data || [];
            setCollectors(users.filter(u => u.role && u.role.includes('collector')));
        }).catch(err => console.error("Error fetching users", err));
    }, [apiService]);

    const [newPayment, setNewPayment] = useState({
        customer_id: '',
        amount: '',
        date: new Date().toISOString().split('T')[0],
        paid: false,
        pre_payment: false,
        reason: ''
    });

    const [customerBalance, setCustomerBalance] = useState({
        unpaid_balance: 0,
        pre_payment_balance: 0,
        total_balance: 0,
        stored_balance: 0
    });

    const [showAddPaymentForm, setShowAddPaymentForm] = useState(false);
    const [showReceiptModal, setShowReceiptModal] = useState(false);
    const [receiptData, setReceiptData] = useState(null);
    const [selectedPayment, setSelectedPayment] = useState(null);

    // --- This is the required state for the combined unpaid statement ---
    const [showCombinedReceiptModal, setShowCombinedReceiptModal] = useState(false);
    const [combinedReceiptData, setCombinedReceiptData] = useState(null);

    // --- NEW: State for the generate future payments modal ---
    const [showGenerateModal, setShowGenerateModal] = useState(false);
    const [generateOptions, setGenerateOptions] = useState({
        customer_id: 'all',
        until: 'end_of_current_month'
    });

    // Helper Functions
    const getStatusColor = (isPaid) => {
        return isPaid ? theme.palette.success.main : theme.palette.error.main;
    };

    const getPaymentTypeColor = (isPrePayment) => {
        return isPrePayment ? theme.palette.secondary.main : theme.palette.primary.main;
    };

    // API Functions wrapped in useCallback for stability
    const fetchCustomersForDropdown = useCallback(async () => {
        try {
            const response = await apiService.fetchCustomers();
            setCustomers(response.customers || []);
        } catch (error) {
            console.error("Error fetching customers for payments view:", error);
            setSnackbar({ open: true, message: 'Failed to load customers for payment entry.', severity: 'error' });
        }
    }, [apiService, setSnackbar]);

    const fetchPayments = useCallback(async () => {
        setLoading(true);
        try {
            const params = { ...filters };
            if (currentTab === 1) { // If on 'Unpaid' tab, force status to unpaid
                params.status = 'unpaid';
            } /*else { // If on 'All Payments' tab, clear status filter
                delete params.status;
            }
            */

            const response = await apiService.fetchPayments(
                params.customer_id,
                params.status || '',
                params.start_date,
                params.end_date,
                params.search_query,
                params.collected_by,
                params.collected_date,
                params.sort_by,
                params.sort_desc
            );
            setPayments(response.data.payments || []); // FIX: Access the 'payments' key from the response
        } catch (error) {
            console.error("Error fetching payments:", error);
            setSnackbar({ open: true, message: 'Failed to load payments.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [filters, currentTab, apiService, setSnackbar]);

    const fetchCustomerBalance = useCallback(async (customerId) => {
        try {
            const response = await apiService.fetchCustomerBalance(customerId);
            setCustomerBalance(response.data);
        } catch (error) {
            console.error("Error fetching customer balance:", error);
            setSnackbar({ open: true, message: 'Failed to load customer balance.', severity: 'error' });
        }
    }, [apiService, setSnackbar]);

    // Initial Load & Filter Effects
    useEffect(() => {
        fetchCustomersForDropdown();
    }, [fetchCustomersForDropdown]);

    useEffect(() => {
        fetchPayments();
        if (filters.customer_id) {
            fetchCustomerBalance(filters.customer_id);
        }
    }, [filters, currentTab, fetchPayments, fetchCustomerBalance]);


    // Event Handlers
    const handleFilterChange = (e) => {
        const { name, value } = e.target;
        setFilters(prev => ({ ...prev, [name]: value }));
    };

    const handleSearchChange = useCallback((value) => {
        setFilters(prev => ({ ...prev, search_query: value }));
    }, []);

    // --- NEW: Handler for generating future payments ---
    const handleGenerateFuturePayments = async () => {
        const today = new Date();
        let untilDate;

        if (generateOptions.until === 'end_of_current_month') {
            untilDate = new Date(today.getFullYear(), today.getMonth() + 1, 0);
        } else { // end_of_next_month
            untilDate = new Date(today.getFullYear(), today.getMonth() + 2, 0);
        }

        const payload = {
            customer_id: generateOptions.customer_id,
            until_date: untilDate.toISOString().split('T')[0]
        };

        try {
            const response = await apiService.generateFuturePayments(payload);
            setSnackbar({ open: true, message: response.data.message, severity: 'success' });
            setShowGenerateModal(false);
            fetchPayments(); // Refresh the list to show new payments
        } catch (error) {
            console.error("Error generating future payments:", error);
            setSnackbar({ open: true, message: 'Failed to generate future payments. ' + (error.response?.data?.error || error.message), severity: 'error' });
        }
    };

    const handleAddPayment = async () => {
        if (!newPayment.customer_id || !newPayment.amount || !newPayment.date || !newPayment.reason) {
            setSnackbar({ open: true, message: 'Please fill out all required fields, including the reason.', severity: 'warning' });
            return;
        }

        const parsedAmount = parseFloat(newPayment.amount);
        if (isNaN(parsedAmount) || parsedAmount <= 0) {
            setSnackbar({ open: true, message: 'Amount must be a positive number.', severity: 'warning' });
            return;
        }
        setLoading(true);
        try {
            const paymentData = {
                ...newPayment,
                amount: parsedAmount,
                pre_payment: newPayment.pre_payment, // Assuming manual payments are pre-payments
                paid: newPayment.pre_payment,
                reason: newPayment.reason
            };

            await apiService.addCustomerPayment(paymentData);
            setSnackbar({ open: true, message: 'Payment added successfully!', severity: 'success' });
            setNewPayment({
                customer_id: '',
                amount: '',
                date: new Date().toISOString().split('T')[0],
                paid: false,
                pre_payment: false,
                reason: ''
            });
            setShowAddPaymentForm(false);
            fetchPayments();
            if (paymentData.customer_id) {
                fetchCustomerBalance(paymentData.customer_id);
            }
        } catch (error) {
            console.error('Error adding payment:', error);
            setSnackbar({ open: true, message: 'An error occurred while adding the payment. ' + (error.response?.data?.error || error.message), severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    // --- Open the mark-as-paid dialog ---
    const openMarkPaidDialog = (payment) => {
        setMarkPaidDialog({ open: true, paymentId: payment.id, outstanding: payment.amount, customerName: payment.customer_name, collectedAmount: payment.collected_amount });
        const defaultAmount = (payment.collected && payment.collected_amount) ? payment.collected_amount : payment.amount;
        setMarkPaidAmount(String(defaultAmount));
    };

    const handleMarkPaid = async (paymentId, currentOutstandingAmount) => {
        const amountInput = markPaidAmount;
        const paymentAmountInput = amountInput !== '' ? amountInput : null;
        const amountReceived = parseFloat(paymentAmountInput);

        if (isNaN(amountReceived) || amountReceived <= 0) {
            setSnackbar({ open: true, message: 'Please enter a valid positive amount.', severity: 'warning' });
            return;
        }
        setMarkPaidDialog({ open: false, paymentId: null, outstanding: 0, customerName: '' });

        const targetPayment = payments.find(p => p.id === paymentId);
        let action = 'pay';
        if (targetPayment && !targetPayment.collected && userRoles.includes('collector') && !userRoles.includes('admin') && !userRoles.includes('finance')) {
            action = 'collect';
        } else if (targetPayment && !targetPayment.collected && (userRoles.includes('admin') || userRoles.includes('finance'))) {
            // Admin can bypass collect and directly pay, or maybe we just do pay.
            action = 'collect'; // Actually, if it's not collected, and admin clicks 'collect', they collect it. Or they can directly mark it paid. The button says 'Collect' for them too if it's not collected. Let's send 'collect'.
            // Wait, let's look at the button label: `payment.collected ? 'Confirm Receipt' : 'Collect'`.
            // So if `!payment.collected` it is ALWAYS 'collect'.
            action = 'collect';
        }

        let payload = { action };
        if (amountReceived < currentOutstandingAmount) {
            payload.partial_payment = true;
            payload.partial_amount = amountReceived;
        } else {
            payload.partial_payment = false;
            payload.partial_amount = amountReceived;
        }

        try {
            const response = await apiService.markPaymentAsPaid(paymentId, payload);
            setSnackbar({ open: true, message: response.data.message, severity: 'success' });

            fetchPayments();
            if (filters.customer_id) {
                fetchCustomerBalance(filters.customer_id);
            }
            if (action === 'collect') {
                return;
            }

            const paidPayment    = payments.find(p => p.id === paymentId) || {};
            const paidCustomer   = customers.find(c => c.id === paidPayment.customer_id) || {};
            const paidCustomerPhone = paidCustomer.phone || '';
            const paidCustomerName  = paidPayment.customer_name || '';

            // ── Auto-open WhatsApp deep link (deep-link mode) ──────────────────
            if (waSettings.enabled && waSettings.mode === 'deeplink') {
                const phone = paidCustomerPhone.replace(/\D/g, '');
                if (phone) {
                    const amountPaid = parseFloat(response.data.amount_received_in_this_transaction || 0).toFixed(2);
                    const msg = (waSettings.deeplink_msg_payment || 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!')
                        .replace('{customer_name}', paidCustomerName)
                        .replace('{amount}', amountPaid);
                    window.open(`https://wa.me/${phone}?text=${encodeURIComponent(msg)}`, '_blank', 'noopener,noreferrer');
                }
            }
            // ──────────────────────────────────────────────────────────────────

        } catch (error) {
            console.error("Error marking payment paid:", error);
            setSnackbar({ open: true, message: 'Failed to mark payment as paid. ' + (error.response?.data?.error || error.message), severity: 'error' });
        }
    };


    const handleDeletePayment = async (paymentId) => {
        if (window.confirm('Are you sure you want to delete this payment? This action cannot be undone.')) {
            try {
                await apiService.deletePayment(paymentId);
                setSnackbar({ open: true, message: 'Payment deleted successfully!', severity: 'success' });
                fetchPayments();
                if (filters.customer_id) {
                    fetchCustomerBalance(filters.customer_id);
                }
            } catch (error) {
                console.error("Error deleting payment:", error);
                setSnackbar({ open: true, message: 'Failed to delete payment. ' + (error.response?.data?.error || error.message), severity: 'error' });
            }
        }
    };

    // --- Bulk delete selected payments ---
    const handleBulkDelete = async () => {
        if (selected.length === 0) return;
        if (!window.confirm(`Delete ${selected.length} selected payment(s)? This cannot be undone.`)) return;
        try {
            await Promise.allSettled(selected.map(id => apiService.deletePayment(id)));
            setSnackbar({ open: true, message: `${selected.length} payment(s) deleted.`, severity: 'success' });
            setSelected([]);
            fetchPayments();
            if (filters.customer_id) fetchCustomerBalance(filters.customer_id);
        } catch (error) {
            setSnackbar({ open: true, message: 'Bulk delete failed: ' + error.message, severity: 'error' });
        }
    };

    // --- Bulk mark selected payments as paid ---
    const handleBulkMarkPaid = async () => {
        if (selected.length === 0) return;
        if (!window.confirm(`Mark ${selected.length} selected payment(s) as paid?`)) return;
        try {
            await Promise.allSettled(selected.map(id => apiService.markPaymentAsPaid(id)));
            setSnackbar({ open: true, message: `${selected.length} payment(s) marked as paid.`, severity: 'success' });
            setSelected([]);
            fetchPayments();
            if (filters.customer_id) fetchCustomerBalance(filters.customer_id);
        } catch (error) {
            setSnackbar({ open: true, message: 'Bulk mark paid failed: ' + error.message, severity: 'error' });
        }
    };

    // --- Selection helpers ---
    const handleSelectAll = (e) => {
        if (e.target.checked) { setSelected(payments.map(p => p.id)); }
        else { setSelected([]); }
    };
    const handleSelectOne = (id) => {
        setSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
    };
    const isSelected = (id) => selected.includes(id);
    const handlePrepareReceipt = async (paymentId) => {
        try {
            const response = await apiService.fetchReceipt(paymentId);
            const data = response.data;
            const customerId = (payments.find(p => p.id === paymentId) || {}).customer_id;

            if (customerId) {
                const balanceResponse = await apiService.fetchCustomerBalance(customerId);
                data.customer_new_balance = balanceResponse.data.stored_balance;
            } else {
                data.customer_new_balance = 0;
            }

            data.amount_paid_now = data.amount_on_record;
            data.remaining_on_payment = data.paid_status !== 'Paid' ? data.amount_on_record : 0;

            setReceiptData(data);
            setShowReceiptModal(true);
        } catch (error) {
            console.error("Error fetching receipt:", error);
            setSnackbar({ open: true, message: 'Failed to fetch receipt. ' + (error.response?.data?.message || error.message), severity: 'error' });
        }
    };

const handlePrint = () => {
    if (!receiptData) return;

    const arabicMonths = [
        'كانون الثاني', 'شباط', 'آذار', 'نيسان', 'أيار', 'حزيران',
        'تموز', 'آب', 'أيلول', 'تشرين الأول', 'تشرين الثاني', 'كانون الأول'
    ];
    const date = new Date(receiptData.payment_date);
    const month = date.getMonth();
    const year = date.getFullYear();
    const arabicMonthYear = `${arabicMonths[month]} ${year}`;
    const currentBalance = (parseFloat(receiptData.customer_new_balance) || 0).toFixed(2);
    const previousBalance = ((parseFloat(receiptData.customer_new_balance) || 0) + (parseFloat(receiptData.amount_paid_now) || 0)).toFixed(2);

    const winPrint = window.open('', '', 'left=0,top=0,width=900,height=400,toolbar=0,scrollbars=0,status=0');
    winPrint.document.write(`
            <!DOCTYPE html>
            <html>
            <head>
                <title>Receipt</title>
                <style>
                    @page {
                        size: 21cm 30cm;
                        margin: 0;
                    }
                    body {
                        margin: 0;
                        font-family: Arial, sans-serif;
                        -webkit-print-color-adjust: exact;
                        print-color-adjust: exact;
                    }
                    .receipt-container {
                        width: 21cm;
                        height: 7.5cm;
                        display: flex !important;
                        direction: rtl;
                        background-color: white;
                        padding-top: 40px;
                        box-sizing: border-box;
                    }
                    /* Force page break after every 4 receipts */
                    .receipt-container:nth-child(4n) {
                        page-break-after: always;
                    }
                    .main-part {
                        width: 14.1cm;
                        padding: 40px 50px 10px 15px;
                        box-sizing: border-box;
                    }
                    .mini-part {
                        width: 6.9cm;
                        padding: 40px 50px 10px 15px;
                        box-sizing: border-box;
                        border-right: 1px dashed #000;
                    }
                    .receipt-details span {
                        display: block;
                        font-size: 16px;
                        margin-bottom: 2px;
                        color: #000;
                    }
                </style>
            </head>
            <body>
                <div class="receipt-container">
                    <div class="main-part">
                        <div class="receipt-details">
                            <span>الإسم: ${receiptData.customer_name}</span>
                            <span>العنوان: ${receiptData.customer_address}</span>
                            <span>الهاتف: ${receiptData.customer_phone}</span>
                            <span>تاريخ الإيصال: ${receiptData.payment_date}</span>
                            <span>الدفعة الشهرية: ${(parseFloat(receiptData.subscription_plan_details?.price) || 0).toFixed(2)}$ - الخدمة: ${receiptData.subscription_plan_details?.name || 'انترنت'}</span>
                            <span>عن شهر: ${arabicMonthYear}</span>
                            <span style="font-weight: bold;">الرصيد الحالي: ${currentBalance}$ - الرصيد السابق: ${previousBalance}$</span>
                        </div>
                    </div>
                    <div class="mini-part">
                        <div class="receipt-details">
                            <span>الإسم: ${receiptData.customer_name}</span>
                            <span>العنوان: ${receiptData.customer_address}</span>
                            <span>الهاتف: ${receiptData.customer_phone}</span>
                            <span>الدفعة الشهرية: ${(parseFloat(receiptData.subscription_plan_details?.price) || 0).toFixed(2)}$</span>
                            <span>الرصيد الحالي: ${currentBalance}$</span>
                            <span>الرصيد السابق: ${previousBalance}$</span>
                        </div>
                    </div>
                </div>
            </body>
            </html>
        `);
    winPrint.document.close();
    winPrint.focus();

    setTimeout(() => {
        winPrint.print();
        winPrint.close();
    }, 250);
    };

    const handlePrintReceipt = async (paymentId) => {
        try {
            const response = await apiService.fetchReceipt(paymentId);
            const data = response.data;

            const customerId = (payments.find(p => p.id === paymentId) || {}).customer_id;
            if (customerId) {
                const balanceResponse = await apiService.fetchCustomerBalance(customerId);
                data.customer_new_balance = balanceResponse.data.stored_balance;
            } else {
                data.customer_new_balance = 0;
            }

            data.amount_paid_now = 0; // Not a new transaction
            data.remaining_on_payment = data.paid_status !== 'Paid' ? data.amount_on_record : 0;

            setReceiptData(data);
            setShowReceiptModal(true);
        } catch (error) {
            console.error("Error fetching receipt:", error);
            setSnackbar({ open: true, message: 'Failed to fetch receipt. ' + (error.response?.data?.message || error.message), severity: 'error' });
        }
    };

    // --- NEW: Handler for printing the combined unpaid statement ---
    const handlePrintUnpaidStatement = async () => {
        if (!filters.customer_id) {
            setSnackbar({ open: true, message: 'Please select a customer first.', severity: 'warning' });
            return;
        }
        try {
            // NOTE: You must add `fetchUnpaidReceipt` to your apiService to call the new endpoint.
            // Example: fetchUnpaidReceipt: (customerId) => api.get(`/customers/${customerId}/unpaid_receipt`),
            const response = await apiService.fetchUnpaidReceipt(filters.customer_id);
            setCombinedReceiptData(response.data);
            setShowCombinedReceiptModal(true);
        } catch (error) {
            console.error("Error fetching unpaid statement:", error);
            setSnackbar({ open: true, message: 'Failed to fetch unpaid statement. ' + (error.response?.data?.message || error.message), severity: 'error' });
        }
    };

    
    const handleTabChange = (event, newValue) => {
        setCurrentTab(newValue);
    };
/*
    const filteredPayments = (Array.isArray(payments) ? payments : []).filter(payment => {
        const matchesCustomer = filters.customer_id ? payment.customer_id === parseInt(filters.customer_id) : true;
        const matchesStatus = (currentTab === 1) ? !payment.paid : true;
        const matchesStartDate = filters.start_date ? new Date(payment.date) >= new Date(filters.start_date) : true;
        const matchesEndDate = filters.end_date ? new Date(payment.date) <= new Date(filters.end_date) : true;
        const matchesSearch = filters.search_query ? (payment.customer_name && payment.customer_name.toLowerCase().includes(filters.search_query.toLowerCase())) : true;
        return matchesCustomer && matchesStatus && matchesStartDate && matchesEndDate && matchesSearch;
    });
    */

    const totalRevenue = React.useMemo(() => getTotalRevenue(payments), [payments]);
    const currentMonthRevenue = React.useMemo(() => getCurrentMonthRevenue(payments), [payments]);

    // PaymentCard wraps the module-level _PaymentCard so React.memo works properly.
    // Handlers are collected into one stable object to minimise re-renders.
    const cardHandlers = React.useMemo(() => ({
        getStatusColor,
        getPaymentTypeColor,
        openMarkPaidDialog,
        handlePrepareReceipt,
        handleDeletePayment,
        buildWhatsAppLink,
        waSettings,
        userRoles,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [waSettings, userRoles.join(',')]);

    const PaymentCard = React.useCallback(
        ({ payment }) => <PaymentCardItem payment={payment} {...cardHandlers} />,
        [cardHandlers]
    );

    return (
        <Box sx={{ p: 3, background: 'linear-gradient(135deg, #f6f9fc 0%, #ffffff 100%)', minHeight: '100vh' }}>
            {/* Header Section */}
            <Paper
                elevation={0}
                sx={{
                    p: { xs: 2, sm: 3, md: 4 },
                    mb: 4,
                    borderRadius: '24px',
                    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                    color: 'white',
                    position: 'relative',
                    overflow: 'hidden'
                }}
            >
                <Box
                    sx={{
                        position: 'absolute',
                        top: -50,
                        right: -50,
                        width: 200,
                        height: 200,
                        borderRadius: '50%',
                        background: alpha('#ffffff', 0.1),
                        filter: 'blur(1px)'
                    }}
                />
                <Box sx={{ position: 'relative', zIndex: 1 }}>
                    <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'flex-start', sm: 'flex-start' }, gap: { xs: 2, sm: 0 }, mb: 3 }}>
                        <Box>
                            <Typography variant="h4" sx={{ fontWeight: 700, mb: 1, fontSize: { xs: '1.3rem', sm: '1.75rem', md: '2.125rem' } }}>
                                Payments Management
                            </Typography>
                            <Typography variant="body1" sx={{ opacity: 0.9, fontSize: { xs: '0.85rem', sm: '1rem' } }}>
                                Track and manage customer payments efficiently
                            </Typography>
                        </Box>
                        <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, gap: { xs: 1, sm: 2 }, width: { xs: '100%', sm: 'auto' } }}>
                            <Button
                                variant="contained"
                                startIcon={<ScheduleSendIcon />}
                                onClick={() => setShowGenerateModal(true)}
                                sx={{
                                    backgroundColor: 'rgba(255, 255, 255, 0.2)',
                                    backdropFilter: 'blur(10px)',
                                    border: '1px solid rgba(255, 255, 255, 0.3)',
                                    color: 'white',
                                    borderRadius: '16px',
                                    textTransform: 'none',
                                    fontWeight: 600,
                                    px: { xs: 2, sm: 3 },
                                    py: { xs: 1, sm: 1.5 },
                                    width: { xs: '100%', sm: 'auto' },
                                    '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' },
                                    transition: 'all 0.3s ease'
                                }}
                            >
                                Generate Future Payments
                            </Button>
                            <Button
                                variant="contained"
                                startIcon={<AddIcon />}
                                onClick={() => setShowAddPaymentForm(true)}
                                sx={{
                                    backgroundColor: 'rgba(255, 255, 255, 0.2)',
                                    backdropFilter: 'blur(10px)',
                                    border: '1px solid rgba(255, 255, 255, 0.3)',
                                    color: 'white',
                                    borderRadius: '16px',
                                    textTransform: 'none',
                                    fontWeight: 600,
                                    px: { xs: 2, sm: 3 },
                                    py: { xs: 1, sm: 1.5 },
                                    width: { xs: '100%', sm: 'auto' },
                                    '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' },
                                    transition: 'all 0.3s ease'
                                }}
                            >
                                Add New Payment
                            </Button>
                        </Box>
                    </Box>

                    {/* Statistics */}
                    <Box sx={{ display: 'flex', gap: { xs: 2, sm: 3 }, alignItems: 'center', flexWrap: 'wrap' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <PaymentIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>
                                    Total Payments
                                </Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                                    {payments.length}
                                </Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <CheckCircleIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>
                                    Paid Payments
                                </Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                                    {payments.filter(p => p.paid).length}
                                </Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <TrendingUpIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>
                                    Current Month Revenue
                                </Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                                    ${currentMonthRevenue.toFixed(2)}
                                </Typography>
                            </Box>
                        </Box>
                        <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                            <TrendingUpIcon sx={{ fontSize: 20 }} />
                            <Box>
                                <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>
                                    Total Revenue
                                </Typography>
                                <Typography variant="h6" sx={{ fontWeight: 700 }}>
                                    ${totalRevenue.toFixed(2)}
                                </Typography>
                            </Box>
                        </Box>
                    </Box>
                </Box>
            </Paper>

            {/* View Toggle + Bulk Actions Bar */}
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2, flexWrap: 'wrap', gap: 1 }}>
                <ToggleButtonGroup value={viewMode} exclusive onChange={(e, v) => v && setViewMode(v)} size="small">
                    <ToggleButton value="grid"><ViewModuleIcon /></ToggleButton>
                    <ToggleButton value="list"><ViewListIcon /></ToggleButton>
                </ToggleButtonGroup>
                {selected.length > 0 && (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>{selected.length} selected</Typography>
                        {(userRoles.includes('admin') || userRoles.includes('finance')) && (
                            <Button size="small" variant="contained" color="success" startIcon={<CheckCircleIcon />} onClick={handleBulkMarkPaid}>Mark Paid</Button>
                        )}
                        {(userRoles.includes('admin') || userRoles.includes('finance')) && (
                            <Button size="small" variant="contained" color="error" startIcon={<DeleteIcon />} onClick={handleBulkDelete}>Delete Selected</Button>
                        )}
                        <Button size="small" variant="outlined" onClick={() => setSelected([])}>Clear</Button>
                    </Box>
                )}
            </Box>

            {/* Filters Section */}
            <Paper
                sx={{
                    p: 3,
                    borderRadius: '16px',
                    background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
                    border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                    mb: 3
                }}
            >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
                    <FilterListIcon sx={{ color: 'text.secondary' }} />
                    <Typography variant="h6" sx={{ fontWeight: 700 }}>
                        Filters & Search
                    </Typography>
                </Box>
                <Grid container spacing={2}>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            select
                            label="Customer"
                            name="customer_id"
                            value={filters.customer_id}
                            onChange={handleFilterChange}
                            size="small"
                        >
                            <MenuItem value="">All Customers</MenuItem>
                            {customers.map(customer => (
                                <MenuItem key={customer.id} value={customer.id}>{customer.name}</MenuItem>
                            ))}
                        </TextField>
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            select
                            label="Status"
                            name="status"
                            value={filters.status}
                            onChange={handleFilterChange}
                            size="small"
                        >
                            <MenuItem value="">All Statuses</MenuItem>
                            <MenuItem value="paid">Paid</MenuItem>
                            <MenuItem value="unpaid">Unpaid</MenuItem>
                        </TextField>
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            type="date"
                            label="Start Date"
                            name="start_date"
                            value={filters.start_date}
                            onChange={handleFilterChange}
                            InputLabelProps={{ shrink: true }}
                            size="small"
                        />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            type="date"
                            label="End Date"
                            name="end_date"
                            value={filters.end_date}
                            onChange={handleFilterChange}
                            InputLabelProps={{ shrink: true }}
                            size="small"
                        />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            select
                            fullWidth
                            label="Collector"
                            name="collected_by"
                            value={filters.collected_by}
                            onChange={handleFilterChange}
                            size="small"
                        >
                            <MenuItem value=""><em>All Collectors</em></MenuItem>
                            {collectors.map(c => (
                                <MenuItem key={c.id} value={c.id}>{c.username}</MenuItem>
                            ))}
                        </TextField>
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            type="date"
                            label="Collected Date"
                            name="collected_date"
                            value={filters.collected_date}
                            onChange={handleFilterChange}
                            InputLabelProps={{ shrink: true }}
                            size="small"
                        />
                    </Grid>
                    <Grid item xs={12}>
                        <DebouncedSearchInput
                            fullWidth
                            label="Search by customer name"
                            value={filters.search_query || ''}
                            onChange={handleSearchChange}
                            InputProps={{
                                startAdornment: <InputAdornment position="start"><SearchIcon sx={{ color: 'text.secondary' }} /></InputAdornment>
                            }}
                            size="small"
                        />
                    </Grid>
                </Grid>
            </Paper>

            {/* Customer Balance Card */}
            {filters.customer_id && (
                <Paper sx={{ p: 3, borderRadius: '16px', background: 'linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%)', color: 'white', mb: 3 }}>
                    <Typography variant="h6" sx={{ fontWeight: 700, mb: 2 }}>
                        Customer Balance Summary
                    </Typography>
                    <Grid container spacing={2} alignItems="center">
                        <Grid item xs={12} sm={3}><Box><Typography variant="caption" sx={{ opacity: 0.8 }}>Unpaid Balance</Typography><Typography variant="h6" sx={{ fontWeight: 700 }}>${(customerBalance.calculated_unpaid_balance || 0).toFixed(2)}</Typography></Box></Grid>
                        <Grid item xs={12} sm={3}><Box><Typography variant="caption" sx={{ opacity: 0.8 }}>Pre-Payment Balance</Typography><Typography variant="h6" sx={{ fontWeight: 700 }}>${(customerBalance.calculated_pre_payment_balance || 0).toFixed(2)}</Typography></Box></Grid>
                        <Grid item xs={12} sm={3}><Box><Typography variant="caption" sx={{ opacity: 0.8 }}>Account Balance</Typography><Typography variant="h6" sx={{ fontWeight: 700 }}>${(customerBalance.stored_balance || 0).toFixed(2)}</Typography><Typography variant="caption" sx={{ opacity: 0.7, fontSize: '0.7rem' }}>{customerBalance.stored_balance < 0 ? 'Customer owes' : 'Customer credit'}</Typography></Box></Grid>
                        {/* --- NEW: Button to print unpaid statement --- */}
                        <Grid item xs={12} sm={3} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end' }}>
                            <Button
                                variant="contained"
                                startIcon={<ReceiptIcon />}
                                onClick={handlePrintUnpaidStatement}
                                disabled={(customerBalance.calculated_unpaid_balance || 0) <= 0}
                                sx={{ mt: { xs: 2, sm: 0 }, backgroundColor: 'white', color: theme.palette.primary.main, '&:hover': { backgroundColor: alpha('#ffffff', 0.9) }, '&.Mui-disabled': { backgroundColor: 'rgba(255,255,255,0.5)' } }}
                            >
                                Print Statement
                            </Button>
                        </Grid>
                    </Grid>
                </Paper>
            )}

            {/* Payments List */}
            {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
                    <CircularProgress size={40} thickness={4} />
                </Box>
            ) : payments.length === 0 ? (
                <Fade in timeout={800}>
                    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', py: 8, textAlign: 'center' }}>
                        <Box sx={{ width: 120, height: 120, borderRadius: '50%', background: `linear-gradient(135deg, ${alpha(theme.palette.primary.main, 0.1)}, ${alpha(theme.palette.secondary.main, 0.1)})`, display: 'flex', alignItems: 'center', justifyContent: 'center', mb: 3 }}>
                            <PaymentIcon sx={{ fontSize: 48, color: theme.palette.primary.main, opacity: 0.7 }} />
                        </Box>
                        <Typography variant="h6" sx={{ color: 'text.secondary', mb: 1 }}>No payments found</Typography>
                        <Typography variant="body2" sx={{ color: 'text.disabled', mb: 3 }}>Adjust your filters or add a new payment to get started</Typography>
                    </Box>
                </Fade>
            ) : viewMode === 'grid' ? (
                <Grid container spacing={3}>
                    {payments.map((payment, index) => (
                        <Grid item xs={12} sm={6} lg={4} key={payment.id}>
                            <PaymentCard payment={payment} index={index} />
                        </Grid>
                    ))}
                </Grid>
            ) : (
                // --- LIST VIEW ---
                <Paper sx={{ borderRadius: '16px', overflow: 'hidden' }}>
                    {selected.length > 0 && (
                        <Toolbar sx={{ bgcolor: alpha(theme.palette.primary.main, 0.08), borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}` }}>
                            <Typography sx={{ flex: 1, fontWeight: 600 }}>{selected.length} selected</Typography>
                            {(userRoles.includes('admin') || userRoles.includes('finance')) && (
                                <Button size="small" variant="contained" color="success" startIcon={<CheckCircleIcon />} onClick={handleBulkMarkPaid} sx={{ mr: 1 }}>Mark Paid</Button>
                            )}
                            {(userRoles.includes('admin') || userRoles.includes('finance')) && (
                                <Button size="small" variant="contained" color="error" startIcon={<DeleteIcon />} onClick={handleBulkDelete}>Delete Selected</Button>
                            )}
                        </Toolbar>
                    )}
                    <TableContainer>
                        <Table size="small">
                            <TableHead sx={{ bgcolor: alpha(theme.palette.primary.main, 0.05) }}>
                                <TableRow>
                                    <TableCell padding="checkbox">
                                        <Checkbox
                                            indeterminate={selected.length > 0 && selected.length < payments.length}
                                            checked={payments.length > 0 && selected.length === payments.length}
                                            onChange={handleSelectAll}
                                        />
                                    </TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Customer</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Billed Date</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Paid Date</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Amount</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Type</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {payments.map((payment) => {
                                    const sel = isSelected(payment.id);
                                    return (
                                        <TableRow
                                            key={payment.id}
                                            hover
                                            selected={sel}
                                            sx={{ '&.Mui-selected': { bgcolor: alpha(theme.palette.primary.main, 0.06) } }}
                                        >
                                            <TableCell padding="checkbox">
                                                <Checkbox checked={sel} onChange={() => handleSelectOne(payment.id)} />
                                            </TableCell>
                                            <TableCell>
                                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                                                    <Avatar sx={{ width: 32, height: 32, fontSize: '0.85rem', fontWeight: 700, background: `linear-gradient(135deg, ${getPaymentTypeColor(payment.pre_payment)}, ${alpha(getPaymentTypeColor(payment.pre_payment), 0.7)})` }}>
                                                        {(payment.customer_name || 'N').charAt(0).toUpperCase()}
                                                    </Avatar>
                                                    <Box>
                                                        <Typography variant="body2" sx={{ fontWeight: 600 }}>{payment.customer_name || '—'}</Typography>
                                                        {payment.customer_address && (
                                                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{payment.customer_address}</Typography>
                                                        )}
                                                    </Box>
                                                </Box>
                                            </TableCell>
                                            <TableCell><Typography variant="body2">{new Date(payment.date).toLocaleDateString()}</Typography></TableCell>
                                            <TableCell><Typography variant="body2">{payment.paid_at ? new Date(payment.paid_at).toLocaleDateString() : '—'}</Typography></TableCell>
                                            <TableCell>
                                                <Typography variant="body2" sx={{ fontWeight: 700, color: getStatusColor(payment.paid) }}>
                                                    ${(parseFloat(payment.amount) || 0).toFixed(2)}
                                                </Typography>
                                            </TableCell>
                                            <TableCell>
                                                <Chip label={payment.paid ? 'Paid' : 'Unpaid'} size="small"
                                                    sx={{ bgcolor: alpha(getStatusColor(payment.paid), 0.1), color: getStatusColor(payment.paid), fontWeight: 600, border: `1px solid ${alpha(getStatusColor(payment.paid), 0.25)}` }}
                                                />
                                            </TableCell>
                                            <TableCell>
                                                {payment.pre_payment ? (
                                                    <Chip label="Pre-Pay" size="small" sx={{ bgcolor: alpha(theme.palette.secondary.main, 0.1), color: theme.palette.secondary.main, fontWeight: 600 }} />
                                                ) : '—'}
                                            </TableCell>
                                            <TableCell sx={{ whiteSpace: 'nowrap' }}>
                                                {!payment.paid && (
                                                    <Tooltip title={payment.collected ? "Confirm Receipt" : "Collect"}>
                                                        <IconButton size="small" color="success" onClick={() => openMarkPaidDialog(payment)}>
                                                            <CheckCircleIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
                                                {(userRoles.includes('admin') || userRoles.includes('finance')) && (
                                                    <Tooltip title="Print Receipt">
                                                        <IconButton size="small" color="primary" onClick={() => handlePrepareReceipt(payment.id)}>
                                                            <PrintIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
                                                {(userRoles.includes('admin') || userRoles.includes('finance')) && waSettings.enabled && waSettings.mode === 'deeplink' && payment.paid && (() => {
                                                    const waLink = buildWhatsAppLink(payment);
                                                    return waLink ? (
                                                        <Tooltip title="Send via WhatsApp">
                                                            <IconButton size="small" component="a" href={waLink} target="_blank" rel="noopener noreferrer" sx={{ color: '#25D366' }}>
                                                                <WhatsAppIcon fontSize="small" />
                                                            </IconButton>
                                                        </Tooltip>
                                                    ) : null;
                                                })()}
                                                {(userRoles.includes('admin') || userRoles.includes('finance')) && (
                                                    <Tooltip title="Delete">
                                                        <IconButton size="small" color="error" onClick={() => handleDeletePayment(payment.id)}>
                                                            <DeleteIcon fontSize="small" />
                                                        </IconButton>
                                                    </Tooltip>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </Paper>
            )}


            {/* Add Payment Dialog */}
            <Dialog open={showAddPaymentForm} onClose={() => setShowAddPaymentForm(false)} maxWidth="sm" fullWidth>
                <DialogTitle>Add New Payment</DialogTitle>
                <DialogContent>
                    <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={12}>
                            <Autocomplete
                                options={customers}
                                getOptionLabel={(option) => option.name || `ID: ${option.id}`}
                                value={customers.find(c => c.id === newPayment.customer_id) || null}
                                onChange={(e, newValue) => {
                                    setNewPayment({ ...newPayment, customer_id: newValue ? newValue.id : '' });
                                }}
                                renderInput={(params) => <TextField {...params} label="Customer" fullWidth variant="outlined" />}
                            />
                        </Grid>
                        <Grid item xs={12} sm={6}>
                            <TextField
                                fullWidth
                                type="number"
                                label="Amount"
                                value={newPayment.amount}
                                onChange={(e) => setNewPayment({ ...newPayment, amount: e.target.value })}
                            />
                        </Grid>
                        <Grid item xs={12} sm={6}>
                            <TextField
                                fullWidth
                                type="date"
                                label="Date"
                                value={newPayment.date}
                                onChange={(e) => setNewPayment({ ...newPayment, date: e.target.value })}
                                InputLabelProps={{ shrink: true }}
                            />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField
                                fullWidth
                                label="Reason"
                                required
                                value={newPayment.reason}
                                onChange={(e) => setNewPayment({ ...newPayment, reason: e.target.value })}
                            />
                        </Grid>
                        <Grid item xs={12}>
                            <FormControlLabel
                                control={
                                    <Checkbox
                                        checked={newPayment.pre_payment}
                                        onChange={(e) => setNewPayment({ ...newPayment, pre_payment: e.target.checked })}
                                    />
                                }
                                label="Pre-Payment"
                            />
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setShowAddPaymentForm(false)}>Cancel</Button>
                    <Button onClick={handleAddPayment} variant="contained">Add Payment</Button>
                </DialogActions>
            </Dialog>

            {/* Mark as Paid Dialog */}
            <Dialog open={markPaidDialog.open} onClose={() => setMarkPaidDialog({ open: false, paymentId: null, outstanding: 0, customerName: '' })} maxWidth="xs" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Update Payment Status</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" sx={{ mb: 2, color: 'text.secondary' }}>
                        Customer: <strong>{markPaidDialog.customerName}</strong><br />
                        Outstanding: <strong>${(markPaidDialog.outstanding || 0).toFixed(2)}</strong>
                    </Typography>
                    <TextField
                        fullWidth
                        autoFocus
                        type="number"
                        label="Amount Received"
                        value={markPaidAmount}
                        onChange={(e) => setMarkPaidAmount(e.target.value)}
                        InputProps={{ inputProps: { min: 0.01, step: 0.01 } }}
                        helperText={parseFloat(markPaidAmount) < markPaidDialog.outstanding ? 'Partial payment — balance will be updated' : 'Full payment'}
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setMarkPaidDialog({ open: false, paymentId: null, outstanding: 0, customerName: '' })}>Cancel</Button>
                    <Button
                        variant="contained"
                        color="success"
                        startIcon={<CheckCircleIcon />}
                        onClick={() => handleMarkPaid(markPaidDialog.paymentId, markPaidDialog.outstanding)}
                    >
                        Confirm Payment
                    </Button>
                </DialogActions>
            </Dialog>

            {/* Receipt Modal */}
            {/* --- UPDATED: Single Receipt Modal with better logic --- */}
            <Modal open={showReceiptModal} onClose={() => setShowReceiptModal(false)}>
                <Box sx={modalStyle}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                        <Typography variant="h6" component="h2" sx={{ fontWeight: 'bold' }}>Print Receipt</Typography>
                        <IconButton onClick={() => setShowReceiptModal(false)} size="small"><CloseIcon /></IconButton>
                    </Box>
                    {receiptData && (
                        <Box sx={{ textAlign: 'center', p: 4 }}>
                            <Typography sx={{ mb: 3 }}>Ready to print receipt for {receiptData.customer_name}.</Typography>
                            <Button variant="contained" color="primary" onClick={handlePrint} startIcon={<PrintIcon />}>
                                Print
                            </Button>
                        </Box>
                    )}
                </Box>
            </Modal>

            {/* --- NEW: Dialog for generating future payments --- */}
            <Dialog open={showGenerateModal} onClose={() => setShowGenerateModal(false)} maxWidth="sm" fullWidth>
                <DialogTitle>Generate Future Payments</DialogTitle>
                <DialogContent>
                    <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={12}>
                            <TextField
                                fullWidth
                                select
                                label="For Customer"
                                value={generateOptions.customer_id}
                                onChange={(e) => setGenerateOptions({ ...generateOptions, customer_id: e.target.value })}
                            >
                                <MenuItem value="all">All Active Customers</MenuItem>
                                {customers.map(customer => (
                                    <MenuItem key={customer.id} value={customer.id}>{customer.name}</MenuItem>
                                ))}
                            </TextField>
                        </Grid>
                        <Grid item xs={12}>
                            <TextField
                                fullWidth
                                select
                                label="Generate Until"
                                value={generateOptions.until}
                                onChange={(e) => setGenerateOptions({ ...generateOptions, until: e.target.value })}
                            >
                                <MenuItem value="end_of_current_month">End of This Month</MenuItem>
                                <MenuItem value="end_of_next_month">End of Next Month</MenuItem>
                            </TextField>
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setShowGenerateModal(false)}>Cancel</Button>
                    <Button onClick={handleGenerateFuturePayments} variant="contained">Generate</Button>
                </DialogActions>
            </Dialog>


            {/* --- NEW: Combined Receipt Modal for Unpaid Statement --- */}
            <Modal open={showCombinedReceiptModal} onClose={() => setShowCombinedReceiptModal(false)}>
                <Box sx={modalStyle} id="combined-receipt-content">
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
                        <Typography variant="h6" component="h2" sx={{ fontWeight: 'bold' }}>Unpaid Statement</Typography>
                        <IconButton onClick={() => setShowCombinedReceiptModal(false)} size="small"><CloseIcon /></IconButton>
                    </Box>
                    {combinedReceiptData && (
                        <Box>
                            <Box sx={{ mb: 2, textAlign: 'center' }}>
                                {combinedReceiptData.business_logo_url && <img src={`${apiService.baseURL}${combinedReceiptData.business_logo_url}`} alt="Business Logo" style={{ height: '48px', marginBottom: '8px' }} />}
                                <Typography variant="h5" sx={{ fontWeight: 'bold' }}>{combinedReceiptData.business_name}</Typography>
                                <Typography variant="body2">{combinedReceiptData.business_address}</Typography>
                                <Typography variant="body2">Mobile: {combinedReceiptData.business_mobile}</Typography>
                            </Box>
                            <Divider sx={{ my: 2 }} />
                            <Box sx={{ mb: 2 }}>
                                <Typography><strong>To:</strong> {combinedReceiptData.customer_name}</Typography>
                                <Typography><strong>Phone:</strong> {combinedReceiptData.customer_phone}</Typography>
                                <Typography><strong>Date:</strong> {combinedReceiptData.statement_date}</Typography>
                            </Box>
                            <Divider sx={{ my: 2 }} />
                            <Typography variant="h6" sx={{ mb: 1 }}>Pending Payments:</Typography>
                            <Paper variant="outlined" sx={{ mb: 2, background: '#f9f9f9' }}>
                                {combinedReceiptData.unpaid_items.map((item, index) => (
                                    <Box key={index} sx={{ display: 'flex', justifyContent: 'space-between', p: 1.5, borderBottom: index !== combinedReceiptData.unpaid_items.length - 1 ? '1px solid #eee' : 'none' }}>
                                        <Box>
                                            <Typography>{item.description}</Typography>
                                            <Typography variant="caption" color="text.secondary">Date: {item.date}</Typography>
                                        </Box>
                                        <Typography sx={{ fontWeight: '500' }}>${item.amount.toFixed(2)}</Typography>
                                    </Box>
                                ))}
                            </Paper>
                            <Box sx={{ textAlign: 'right' }}>
                                <Typography variant="h6"><strong>Total Due:</strong> <span style={{ color: theme.palette.error.main, fontWeight: 'bold' }}>${combinedReceiptData.total_unpaid_balance.toFixed(2)}</span></Typography>
                                <Typography><strong>Current Account Balance:</strong> <span style={{ fontWeight: 'bold' }}>${combinedReceiptData.customer_current_balance.toFixed(2)}</span></Typography>
                            </Box>
                            <Button variant="contained" color="primary" onClick={() => window.print()} sx={{ mt: 3, width: '100%' }}>Print Statement</Button>
                        </Box>
                    )}
                </Box>
            </Modal>
            
        </Box>
    );
};

export default PaymentsView;
