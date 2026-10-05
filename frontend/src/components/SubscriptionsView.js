import React, { useState, useEffect, useCallback } from 'react';
import useWindowRows, { chunkRows } from './useWindowRows';
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
    Grid,
    Divider,
    alpha,
    useTheme,
    useMediaQuery,
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
    FormControlLabel,
    Tabs,
    Tab
} from '@mui/material';
import {
    Add as AddIcon,
    Person as PersonIcon,
    Phone as PhoneIcon,
    LocationOn as LocationOnIcon,
    Cloud as CloudIcon,
    StickyNote2Outlined as NotesIcon,
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
    Receipt as ReceiptIcon,
    UploadFile as UploadFileIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import { formatStamp } from './formatStamp';
import { mergeNetworkStatus } from './mergeNetworkStatus';
import pollNetworkJob from './pollNetworkJob';
import CustomerImportWizard from './CustomerImportWizard';
import ReceivePaymentDialog from './ReceivePaymentDialog';
import BalanceLogTable from './BalanceLogTable';

// Status of one Payment row, shared by the expanded grid row and the Payments History dialog.
const renderPaymentStatusChip = (p) => {
    if (p.is_refund) return <Chip label="Refund" size="small" variant="outlined" />;
    if (p.pre_payment) return <Chip label="Credit received" size="small" color="info" variant="outlined" />;
    if (p.paid && p.is_gratis) return <Chip label="Gratis" size="small" variant="outlined" />;
    if (p.paid && p.settled_from_credit) return <Chip label="Paid from credit" size="small" color="success" variant="outlined" />;
    if (p.paid) return <Chip label="Paid" size="small" color="success" variant="outlined" />;
    if (p.collected) return <Chip label={`Collected $${(p.collected_amount ?? p.amount).toFixed(2)}`} size="small" color="warning" variant="outlined" />;
    return <Chip label="Unpaid" size="small" color="error" variant="outlined" />;
};

// Whether the Network Status panel should show the finished-state chips.
// mergeNetworkStatus's `pending` flag stays true until BOTH the secret_status
// and active_session polls have landed -- in agent mode that can take a few
// 2-second rounds (see fetchNetworkStatus below) -- so gating on `pending`
// too, not just on the result being non-null, is what stops a still-checking
// poll from being rendered as a finished "Not connected" answer.
export function shouldShowNetworkStatusChips(mikrotikStatus, hasNetworkModule = true) {
    return hasNetworkModule && !!mikrotikStatus && !mikrotikStatus.pending;
}

// --- NEW: Toolbar for bulk actions ---
const getStatusColor = (isActive) => (isActive ? '#10B981' : '#EF4444');
const getPlanColor = (planName) => {
    const colors = { 'basic': '#4F46E5', 'premium': '#10B981', 'pro': '#F59E0B', 'enterprise': '#8B5CF6', 'default': '#6B7280' };
    return colors[planName?.toLowerCase()] || colors.default;
};

const EXPIRY_DAYS = Array.from({ length: 31 }, (_, i) => i + 1);

const GRID_ROW_GAP_PX = 24;

// Shared empty list so a collapsed card's `payments` prop never changes.
const NO_PAYMENTS = [];

// One customer card / table row, memoized: SubscriptionsView holds a lot of
// state (dialogs, form fields, search, selection...) and every change used to
// rebuild every card -- ~12ms each, so 25 cards froze each click for ~0.3s
// and 100 for ~1.3s. Now a card re-renders only when its own props change.
// `actions` is a stable object (see cardActions) whose methods forward to the
// parent's latest handlers; isSyncing/networkMode are props (not read from
// the parent's closure) so the upstream chip still updates when they change.
const GridCustomerCard = React.memo(function GridCustomerCard({
    customer, isExpanded, payments, loadingPayments, isSyncing, networkMode,
    canServeAtDesk, canManageSubscriptions, actions,
}) {
    const theme = useTheme();
    const plan = customer.subscription_plan;
    return (
                <Card sx={{ position: 'relative', overflow: 'visible', transition: 'all 0.3s', '&:hover': { transform: 'translateY(-4px)', boxShadow: `0 12px 24px ${alpha(theme.palette.common.black, 0.15)}` }, borderRadius: '16px', border: `1px solid ${alpha(theme.palette.divider, 0.08)}` }}>
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
                                    {customer.upstream_provider_name && (
                                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 0.5 }}><CloudIcon sx={{ fontSize: 14, color: 'text.secondary' }} /><Typography variant="body2" color="text.secondary">{customer.upstream_provider_name}</Typography></Box>
                                    )}
                                    {customer.notes && (
                                        <Tooltip title={<span style={{ whiteSpace: 'pre-wrap' }}>{customer.notes}</span>} placement="bottom-start">
                                            <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mt: 0.5 }}>
                                                <NotesIcon sx={{ fontSize: 14, color: 'text.secondary', mt: '3px' }} />
                                                {/* Clamped to 3 lines so a long note can't stretch the card; full text in the tooltip. */}
                                                <Typography variant="body2" color="text.secondary"
                                                    sx={{ fontStyle: 'italic', whiteSpace: 'pre-wrap', wordBreak: 'break-word', display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                                                    {customer.notes}
                                                </Typography>
                                            </Box>
                                        </Tooltip>
                                    )}
                                </Box>
                            </Box>
                            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-end' }}>
                                {canServeAtDesk && (
                                    <FormControlLabel
                                        control={<Switch size="small" checked={customer.whatsapp_notifications_enabled !== false} onChange={() => actions.handleToggleWA(customer)} color="primary" />}
                                        label={<Typography variant="caption" sx={{ fontWeight: 600 }}>WA Alerts</Typography>}
                                        labelPlacement="start"
                                        sx={{ m: 0 }}
                                    />
                                )}
                                <Chip label={customer.is_subscription_active ? 'Active' : 'Canceled'} size="small" sx={{ backgroundColor: alpha(getStatusColor(customer.is_subscription_active), 0.1), color: getStatusColor(customer.is_subscription_active), fontWeight: 600, fontSize: '0.75rem', border: `1px solid ${alpha(getStatusColor(customer.is_subscription_active), 0.2)}` }} />
                                {canServeAtDesk && (
                                    <Chip label={`Balance: $${customer.balance.toFixed(2)}`} size="small" sx={{ backgroundColor: alpha(customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, 0.1), color: customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, fontWeight: 600, fontSize: '0.75rem', border: `1px solid ${alpha(customer.balance >= 0 ? theme.palette.success.main : theme.palette.error.main, 0.2)}` }} />
                                )}
                                {actions.renderUpstreamStatusChip(customer, isSyncing, networkMode)}
                            </Box>
                        </Box>
                        <Divider sx={{ my: 2, opacity: 0.6 }} />
                        <Grid container spacing={2} sx={{ mb: 2 }}>
                            <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Plan</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>{plan?.name || 'N/A'}</Typography></Box></Grid>
                            <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Price</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>${((plan?.price || 0) - customer.discount).toFixed(2)}</Typography></Box></Grid>
                            <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Start Date</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>{new Date(customer.subscription_start_date).toLocaleDateString()}</Typography></Box></Grid>
                            <Grid item xs={6}><Box><Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>Expiry Date</Typography><Typography variant="body2" sx={{ fontWeight: 600 }}>{new Date(customer.subscription_expiry_date).toLocaleDateString()}</Typography></Box></Grid>
                        </Grid>
                        {canServeAtDesk && (
                            <>
                                <Divider sx={{ my: 2, opacity: 0.6 }} />
                                <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                                    <Button size="small" variant="outlined" startIcon={isExpanded ? <VisibilityOffIcon /> : <VisibilityIcon />} onClick={() => actions.fetchCustomerPayments(customer.id, customer)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Payments</Button>
                                    <Button size="small" variant="outlined" color="info" startIcon={<EditIcon />} onClick={() => actions.openEditCustomerDialog(customer)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Edit</Button>
                                    <Button size="small" variant="outlined" color="success" startIcon={<RefreshIcon />} onClick={() => actions.renew(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Renew</Button>
                                    {actions.hasWhatsApp && <Button size="small" variant="outlined" color="primary" startIcon={<ChatIcon />} onClick={() => actions.handleSendWAReminder(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>WA Reminder</Button>}
                                    {customer.is_subscription_active ? (
                                        <Button size="small" variant="outlined" color="warning" startIcon={<CancelIcon />} onClick={() => actions.cancel(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Cancel</Button>
                                    ) : (
                                        <Button size="small" variant="outlined" color="success" startIcon={<PlayArrowIcon />} onClick={() => actions.activate(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Activate</Button>
                                    )}
                                    {canManageSubscriptions && (
                                        <Button size="small" variant="outlined" color="error" startIcon={<DeleteIcon />} onClick={() => actions.handleDeleteCustomer(customer.id)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Delete</Button>
                                    )}
                                </Box>
                            </>
                        )}
                        <Collapse in={isExpanded} unmountOnExit>
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
                                                        <TableCell>{renderPaymentStatusChip(p)}</TableCell>
                                                        <TableCell>{actions.renderPaymentAction(p)}</TableCell>
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
    );
});

const ListCustomerRow = React.memo(function ListCustomerRow({
    customer, index, isItemSelected, isSyncing, networkMode,
    canServeAtDesk, canManageSubscriptions, actions, measureRef,
}) {
    const theme = useTheme();
    const labelId = `enhanced-table-checkbox-${index}`;
    const plan = customer.subscription_plan;
    return (
        <TableRow
            ref={measureRef}
            data-index={index}
            hover
            onClick={canServeAtDesk ? (event) => actions.handleSelectClick(event, customer.id) : undefined}
            role="checkbox"
            aria-checked={isItemSelected}
            tabIndex={-1}
            key={customer.id}
            selected={isItemSelected}
            sx={{ cursor: canServeAtDesk ? 'pointer' : 'default', '&.Mui-selected': { backgroundColor: alpha(theme.palette.primary.main, 0.08) } }}
        >
            {canServeAtDesk && (
                <TableCell padding="checkbox">
                    <Checkbox
                        color="primary"
                        checked={isItemSelected}
                        inputProps={{ 'aria-labelledby': labelId }}
                    />
                </TableCell>
            )}
            <TableCell component="th" id={labelId} scope="row" padding="none">
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, p: 1 }}>
                    <Avatar sx={{ background: `linear-gradient(135deg, ${getPlanColor(plan?.name)}, ${alpha(getPlanColor(plan?.name), 0.7)})` }}>
                        {customer.name.charAt(0).toUpperCase()}
                    </Avatar>
                    <Box>
                        <Typography variant="body1" sx={{ fontWeight: 600 }}>{customer.name}</Typography>
                        <Typography variant="body2" color="text.secondary">{customer.address}</Typography>
                        {customer.upstream_provider_name && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>Upstream: {customer.upstream_provider_name}</Typography>}
                        {customer.notes && (
                            <Tooltip title={<span style={{ whiteSpace: 'pre-wrap' }}>{customer.notes}</span>}>
                                <Typography variant="caption" color="text.secondary"
                                    sx={{ display: 'block', fontStyle: 'italic', maxWidth: 280, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    Note: {customer.notes}
                                </Typography>
                            </Tooltip>
                        )}
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
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, alignItems: 'flex-start' }}>
                    <Chip
                        label={customer.is_subscription_active ? 'Active' : 'Canceled'}
                        size="small"
                        sx={{
                            backgroundColor: alpha(getStatusColor(customer.is_subscription_active), 0.1),
                            color: getStatusColor(customer.is_subscription_active),
                            fontWeight: 600,
                        }}
                    />
                    {actions.renderUpstreamStatusChip(customer, isSyncing, networkMode)}
                </Box>
            </TableCell>
            {canServeAtDesk && (
                <TableCell onClick={(e) => e.stopPropagation()}>
                    <Switch size="small" checked={customer.whatsapp_notifications_enabled !== false} onChange={() => actions.handleToggleWA(customer)} color="primary" />
                </TableCell>
            )}
            {canServeAtDesk && (
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
            )}
            <TableCell>{new Date(customer.subscription_expiry_date).toLocaleDateString()}</TableCell>
            {canServeAtDesk && (
                <TableCell onClick={(e) => e.stopPropagation()} sx={{ whiteSpace: 'nowrap' }}>
                    {/* Stop propagation so clicking buttons doesn't select the row */}
                    <Tooltip title="Payments History">
                        <IconButton size="small" color="secondary" onClick={() => actions.fetchCustomerPayments(customer.id, customer)}>
                            <ReceiptIcon fontSize="small" />
                        </IconButton>
                    </Tooltip>
                    <Tooltip title="Edit">
                        <IconButton size="small" color="info" onClick={() => actions.openEditCustomerDialog(customer)}>
                            <EditIcon fontSize="small" />
                        </IconButton>
                    </Tooltip>
                    <Tooltip title="Renew">
                        <IconButton size="small" color="success" onClick={() => actions.renew(customer.id)}>
                            <RefreshIcon fontSize="small" />
                        </IconButton>
                    </Tooltip>
                    {actions.hasWhatsApp && (
                    <Tooltip title="WA Reminder">
                        <IconButton size="small" color="primary" onClick={() => actions.handleSendWAReminder(customer.id)}>
                            <ChatIcon fontSize="small" />
                        </IconButton>
                    </Tooltip>
                    )}
                    {customer.is_subscription_active ? (
                        <Tooltip title="Cancel">
                            <IconButton size="small" color="warning" onClick={() => actions.cancel(customer.id)}>
                                <CancelIcon fontSize="small" />
                            </IconButton>
                        </Tooltip>
                    ) : (
                        <Tooltip title="Activate">
                            <IconButton size="small" color="success" onClick={() => actions.activate(customer.id)}>
                                <PlayArrowIcon fontSize="small" />
                            </IconButton>
                        </Tooltip>
                    )}
                    {canManageSubscriptions && (
                        <Tooltip title="Delete">
                            <IconButton size="small" color="error" onClick={() => actions.handleDeleteCustomer(customer.id)}>
                                <DeleteIcon fontSize="small" />
                            </IconButton>
                        </Tooltip>
                    )}
                </TableCell>
            )}
        </TableRow>
    );
});

const EnhancedTableToolbar = ({ numSelected, onRenew, onCancel, onDelete, disabled }) => {
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
                            startIcon={disabled ? <CircularProgress size={16} /> : <RefreshIcon />}
                            onClick={onRenew}
                            size="small"
                            disabled={disabled}
                        >
                            Renew
                        </Button>
                    </Tooltip>
                    <Tooltip title="Cancel Selected">
                        <Button
                            variant="outlined"
                            color="warning"
                            startIcon={disabled ? <CircularProgress size={16} /> : <CancelIcon />}
                            onClick={onCancel}
                            size="small"
                            disabled={disabled}
                        >
                            Cancel
                        </Button>
                    </Tooltip>
                    {onDelete && (
                        <Tooltip title="Delete Selected">
                            <Button
                                variant="outlined"
                                color="error"
                                startIcon={disabled ? <CircularProgress size={16} /> : <DeleteIcon />}
                                onClick={onDelete}
                                size="small"
                                disabled={disabled}
                            >
                                Delete
                            </Button>
                        </Tooltip>
                    )}
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

// Staff-facing translations of upstream_portal.py's failure-reason enum
// (see the spec's Error Handling table) -- raw reasons like "auth_failed"
// are meaningless jargon to a non-developer.
const UPSTREAM_SYNC_ERROR_MESSAGES = {
    auth_failed: "Couldn't log into the upstream portal — check the provider's portal credentials.",
    not_found: "No subscriber found on the upstream portal matching this username — check for a typo or a renamed account.",
    timeout: "The upstream portal didn't respond in time — try again shortly.",
    scrape_failed: "The upstream portal's page format may have changed, or an unexpected match was found — this needs a code fix, not a data fix.",
};

const SubscriptionsView = ({
    customers,
    pagination,
    subscriptionPlans,
    businessSettings,
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
    setCustomerResellerId,
    customerStatus,
    setCustomerStatus,
    customerExpiryDay,
    setCustomerExpiryDay
}) => {
    const theme = useTheme();
    const { apiService, user, hasModule } = useAppContext();
    // 'employee'/'collector' (and anyone else without admin/finance) get a
    // read-only view of Subscriptions: status only, no balance, no header
    // stats, no action buttons -- enforced here for the UI and separately
    // on the backend (admin_or_finance_required() in app.py) for the
    // actions themselves, so hiding these isn't just cosmetic. A combined
    // role (e.g. "employee,collector") stays read-only too, since this is a
    // positive check for admin/finance, not an absence check for the
    // restricted roles.
    const userRoles = user?.role ? user.role.split(',').map(r => r.trim().toLowerCase()) : [];
    const canManageSubscriptions = userRoles.includes('admin') || userRoles.includes('finance');
    const isAdmin = userRoles.includes('admin');
    // 'cashier' (office front desk) sits between the two: adds and fully
    // edits customers, renews/cancels/activates (single and bulk), and
    // *collects* payments -- but can't record money as received (confirm a
    // payment or edit the balance), delete, or export. Mirrored on the
    // backend by subscription_desk_required() and _is_cashier_only().
    const isCashierOnly = !canManageSubscriptions && userRoles.includes('cashier');
    const canServeAtDesk = canManageSubscriptions || isCashierOnly;
    const [showAddCustomerForm, setShowAddCustomerForm] = useState(false);
    const [importOpen, setImportOpen] = useState(false);
    const [newCustomer, setNewCustomer] = useState({
        name: '',
        phone: '',
        address: '',
        subscription_plan_id: '',
        reseller_id: '',
        upstream_provider_id: '',
        upstream_username: '',
        network_device_id: '',
        pppoe_username: '',
        onu_mac_address: '',
        cpe_mac_address: '',
        discount: 0.0,
        cost_override: '',
        subscription_start_date: new Date().toISOString().split('T')[0],
        additional_payment_amount: 0.0,
        notes: '',
    });
    const [expandedCustomerId, setExpandedCustomerId] = useState(null);
    const [paymentsModalCustomer, setPaymentsModalCustomer] = useState(null);
    const [paymentsModalTab, setPaymentsModalTab] = useState(0); // 0 bills, 1 statement
    const [receiveOpen, setReceiveOpen] = useState(false);
    const [confirmingCollected, setConfirmingCollected] = useState(false);
    const [statementVersion, setStatementVersion] = useState(0);
    const [payments, setPayments] = useState([]);
    const [loadingPayments, setLoadingPayments] = useState(false);
    const [waReminderDialog, setWaReminderDialog] = useState({ open: false, customer: null });
    const [waReminderType, setWaReminderType] = useState('payment_reminder');
    const [waSettings, setWaSettings] = useState({ enabled: false, mode: 'deeplink', deeplink_msg_renewal: 'Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!' });

    useEffect(() => {
        apiService.fetchWhatsAppDeeplinkSettings().then(res => {
            if (res.data?.settings) setWaSettings(res.data.settings);
        }).catch(() => {});
    }, [apiService]);

    const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');
    const [editDialogOpen, setEditDialogOpen] = useState(false);
    const [editingCustomer, setEditingCustomer] = useState(null);
    // Snapshot of onu_mac_address as of the moment the edit dialog was opened.
    // editingCustomer itself can be a stale row (the customers list is only
    // refetched on mount/page/search/sort -- not when the Network Tree page's
    // label-matcher `/apply` links a customer's ONU out-of-band and the user
    // then navigates back here), so we can't tell "untouched" from "cleared"
    // by looking at editingCustomer alone. Comparing against this snapshot
    // lets handleUpdateCustomer omit the field entirely when the user never
    // touched it, instead of always sending it (which would silently unlink
    // the ONU whenever the snapshot predates an out-of-band Apply).
    const editingOnuMacSnapshotRef = React.useRef('');
    // Same pattern, same reason, for cpe_mac_address: a concurrent Locate
    // Customers run on the Network Tree page (or another admin's edit) can
    // rewrite a customer's onu_mac_address/onu_last_seen_at between this
    // dialog opening and being saved, but cpe_mac_address is a distinct
    // field the operator types by hand here -- it needs its own snapshot so
    // an untouched CPE field is never sent (and never silently clobbers a
    // concurrent write to some other field on the same row).
    const editingCpeMacSnapshotRef = React.useRef('');

    // --- NEW STATE ---
    const [viewMode, setViewMode] = useState('grid'); // 'grid' or 'list'
    // Status filter lives in App.js with the other list filters and is
    // applied by the server (see get_customers' `status`).
    const statusFilter = customerStatus || 'active';
    const [resellers, setResellers] = useState([]);
    const [sectors, setSectors] = useState([]);
    const [upstreamProviders, setUpstreamProviders] = useState([]);
    const [networkDevices, setNetworkDevices] = useState([]);

    // Network-status panel (Concept B -- see docs/superpowers/specs/2026-08-12-network-enforcement-design.md)
    const [mikrotikStatus, setMikrotikStatus] = useState(null);
    const [mikrotikStatusLoading, setMikrotikStatusLoading] = useState(false);
    // Which customer currently has a suspend/unsuspend in flight, or null.
    // Deliberately not a bare boolean: a relayed write waits on the agent for
    // seconds (up to pollNetworkJob's ceiling), and a shared flag would leave
    // a DIFFERENT customer's buttons disabled for the duration -- reachable
    // by suspending one customer, closing the dialog, and opening another.
    const [mikrotikActionCustomerId, setMikrotikActionCustomerId] = useState(null);
    const [upstreamSyncStatus, setUpstreamSyncStatus] = useState(null);
    const [upstreamSyncLoading, setUpstreamSyncLoading] = useState(false);
    // Same supersession guard as NetworkDeviceManagementView's handleCheckNow/
    // handleTestConnection: mikrotikStatus/mikrotikStatusLoading are scalars
    // shared by whichever single customer's edit dialog is open, and
    // fetchNetworkStatus's poll can run for up to ~15s in agent mode -- long
    // enough to outlive the dialog it started in (closed, or reopened for a
    // different customer) or to overlap a second Refresh click on the same
    // customer. activeNetworkStatusCustomerIdRef is the customer the shared
    // state currently belongs to; it's set to null (not just left stale)
    // whenever the panel itself is reset below, so a poll still resolving
    // from before that reset can't repaint over it. networkStatusSeqRef lets
    // a newer poll for the SAME customer supersede an older one still in
    // flight, same idea as checkSeqRef there.
    const networkStatusSeqRef = React.useRef({});
    const activeNetworkStatusCustomerIdRef = React.useRef(null);

    useEffect(() => {
        apiService.fetchSectors().then(res => setSectors(res.data)).catch(err => console.error("Failed to load sectors", err));
        // Id/name pickers only -- this endpoint is open to the cashier role,
        // unlike the full /resellers, /upstream-providers, /network-devices.
        apiService.fetchCustomerFormOptions().then(res => {
            setResellers(res.data.resellers || []);
            setUpstreamProviders(res.data.upstream_providers || []);
            setNetworkDevices((res.data.network_devices || []).filter(d => d.device_type !== 'vsol_olt'));
        }).catch(err => console.error("Failed to load customer form options", err));
    }, []);
    const [selected, setSelected] = useState([]); // Array of customer IDs
    const [bulkActionLoading, setBulkActionLoading] = useState(false);


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


    // 'blocked'/'near_expiry'/'quota_exceeded' are Krypton-only values (see
    // upstream_portal_krypton.py) -- deliberately NOT copying Krypton's own
    // portal colors (it renders blocked=orange, offline=light blue) since
    // this app's status colors already mean the same thing across every
    // upstream product (offline=red, expired=orange from the PROradius
    // work) regardless of any one portal's own theme.
    const getUpstreamStatusColor = (status) => ({
        online: '#10B981',
        offline: '#EF4444',
        expired: '#F59E0B',
        blocked: '#DC2626',
        near_expiry: '#EAB308',
        quota_exceeded: '#8B5CF6',
    }[status] || '#6B7280');

    // Single source of truth for both the list-row chip and the Edit
    // dialog's "Network Status" chip -- the two used to be colored
    // independently and only one got updated for Krypton's new values,
    // leaving the dialog rendering blocked/near_expiry/quota_exceeded as a
    // plain grey chip with no error. Also fixes the raw snake_case label
    // ("quota_exceeded") that was otherwise shown verbatim in the UI.
    const UPSTREAM_STATUS_LABELS = {
        online: 'Online',
        offline: 'Offline',
        expired: 'Expired',
        blocked: 'Blocked',
        near_expiry: 'Near Expiry',
        quota_exceeded: 'Quota Exceeded',
        unknown: 'Unknown',
    };
    const getUpstreamStatusLabel = (status) => UPSTREAM_STATUS_LABELS[status] || status || 'unknown';

    // Lets anyone who can SEE the chip (including 'employee', who has no
    // Edit access at all) trigger a fresh live check without it -- this
    // endpoint only ever updates status/expiry fields, never balance or
    // subscription state, so it was never one of the actions employees are
    // restricted from.
    const [syncingCustomerIds, setSyncingCustomerIds] = useState(new Set());
    const handleQuickRefreshUpstreamStatus = async (customerId) => {
        setSyncingCustomerIds(prev => new Set(prev).add(customerId));
        try {
            const response = await apiService.syncCustomerUpstreamStatus(customerId);
            setSnackbar({
                open: true,
                message: response.data?.ok
                    ? `Upstream status: ${response.data.upstream_last_status || 'unknown'}`
                    : (response.data?.error || 'Failed to sync upstream status'),
                severity: response.data?.ok ? 'success' : 'error',
            });
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || error.response?.data?.message || 'Failed to sync upstream status', severity: 'error' });
        } finally {
            setSyncingCustomerIds(prev => { const next = new Set(prev); next.delete(customerId); return next; });
            refetchCustomers();
        }
    };

    // Last-synced upstream status + drift, shown directly on the list/grid so
    // staff don't have to open Edit just to see it (the data is already on
    // the customer object from the list API). The refresh icon here is the
    // only trigger for a fresh live check available to 'employee'; Edit's
    // own "Refresh Upstream Status" panel still exists for admin/finance.
    const renderUpstreamStatusChip = (customer) => {
        if (!hasModule('upstream_sync') || businessSettings?.network_mode !== 'upstream_bridge' || !customer.upstream_provider_id || !customer.upstream_username) {
            return null;
        }
        const isSyncing = syncingCustomerIds.has(customer.id);
        const refreshButton = (
            <Tooltip title="Refresh upstream status">
                <span>
                    <IconButton size="small" onClick={(e) => { e.stopPropagation(); handleQuickRefreshUpstreamStatus(customer.id); }} disabled={isSyncing} sx={{ p: 0.25 }}>
                        {isSyncing ? <CircularProgress size={14} /> : <RefreshIcon sx={{ fontSize: 14 }} />}
                    </IconButton>
                </span>
            </Tooltip>
        );
        if (!customer.upstream_last_synced_at) {
            return (
                <Box sx={{ display: 'flex', gap: 0.25, alignItems: 'center' }}>
                    <Chip size="small" variant="outlined" label="Upstream: not synced" sx={{ fontSize: '0.7rem' }} />
                    {refreshButton}
                </Box>
            );
        }
        const color = getUpstreamStatusColor(customer.upstream_last_status);
        const alertDrift = customer.upstream_drift?.severity === 'alert';
        return (
            <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center', flexWrap: 'wrap' }}>
                <Chip
                    size="small"
                    label={`Upstream: ${getUpstreamStatusLabel(customer.upstream_last_status)}`}
                    sx={{ backgroundColor: alpha(color, 0.1), color, fontWeight: 600, fontSize: '0.7rem', border: `1px solid ${alpha(color, 0.2)}` }}
                />
                {customer.upstream_drift && (
                    <Chip
                        size="small"
                        label={alertDrift ? `⚠ ${customer.upstream_drift.days}d early` : `+${customer.upstream_drift.days}d`}
                        sx={{
                            backgroundColor: alpha(alertDrift ? '#EF4444' : '#3B82F6', 0.1),
                            color: alertDrift ? '#EF4444' : '#3B82F6',
                            fontWeight: 600,
                            fontSize: '0.7rem',
                            border: `1px solid ${alpha(alertDrift ? '#EF4444' : '#3B82F6', 0.2)}`,
                        }}
                    />
                )}
                {refreshButton}
            </Box>
        );
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
            refetchCustomers(); // Refetch customer list to update balance
        } catch (error) {
            console.error("Error marking payment paid:", error);
            setSnackbar({ open: true, message: 'Failed to mark payment as paid. ' + (error.response?.data?.error || error.message), severity: 'error' });
        }
    }, [apiService, setSnackbar, expandedCustomerId, fetchCustomerPayments, refetchCustomers]);

    // Cashier counterpart of handleMarkPaid: records the cash as collected
    // (action 'collect'); finance/admin confirm receipt later on Payments.
    // The cashier's Collect used to be a browser prompt() (amount only); it's a
    // dialog now so the cashier can also say how the money arrived.
    const [collectDialog, setCollectDialog] = useState({ open: false, paymentId: null, outstanding: 0, amount: '', method: 'cash', reference: '' });
    const [collectSubmitting, setCollectSubmitting] = useState(false);
    const closeCollectDialog = () => setCollectDialog({ open: false, paymentId: null, outstanding: 0, amount: '', method: 'cash', reference: '' });

    const handleCollectPayment = useCallback((paymentId, currentAmount) => {
        setCollectDialog({ open: true, paymentId, outstanding: currentAmount, amount: currentAmount.toFixed(2), method: 'cash', reference: '' });
    }, []);

    const submitCollectPayment = useCallback(async () => {
        const { paymentId, outstanding: currentAmount, method, reference } = collectDialog;
        const amountCollected = parseFloat(collectDialog.amount);
        if (isNaN(amountCollected) || amountCollected <= 0) {
            setSnackbar({ open: true, message: 'Please enter a valid positive amount.', severity: 'warning' });
            return;
        }
        if (collectSubmitting) return;
        setCollectSubmitting(true);
        try {
            const response = await apiService.markPaymentAsPaid(paymentId, {
                action: 'collect',
                partial_payment: amountCollected < currentAmount,
                partial_amount: amountCollected,
                method,
                ...(method === 'whish_transfer' && reference.trim() ? { reference: reference.trim() } : {}),
            });
            closeCollectDialog();
            setSnackbar({ open: true, message: response.data.message, severity: 'success' });

            // Deep-link mode: open the "payment received" message, same as
            // collecting from the Payments page. (API mode sends the template
            // server-side in mark_payment_as_paid.)
            const payment = (payments || []).find(p => p.id === paymentId) || {};
            const customer = paymentsModalCustomer
                || (customers || []).find(c => c.id === (payment.customer_id ?? expandedCustomerId)) || {};
            const phone = (customer.phone || '').replace(/\D/g, '');
            if (waSettings.enabled && waSettings.mode === 'deeplink' && phone) {
                const msg = (waSettings.deeplink_msg_payment || 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!')
                    .replace('{customer_name}', customer.name || '')
                    .replace('{amount}', amountCollected.toFixed(2));
                const waUrl = `https://wa.me/${phone}?text=${encodeURIComponent(msg)}`;
                try {
                    window.open(waUrl, '_blank', 'noopener,noreferrer');
                } catch (e) {
                    console.warn('Popup blocked, use snackbar link:', e);
                }
                setSnackbar({
                    open: true,
                    message: `${response.data.message || 'Payment collected!'} — WhatsApp link ready`,
                    severity: 'success',
                    action: (
                        <Button color="inherit" size="small" onClick={() => window.open(waUrl, '_blank', 'noopener,noreferrer')} sx={{ fontWeight: 700, textDecoration: 'underline' }}>
                            Open WhatsApp
                        </Button>
                    )
                });
            }

            if (paymentsModalCustomer) {
                fetchCustomerPayments(paymentsModalCustomer.id, paymentsModalCustomer);
            } else if (expandedCustomerId) {
                // fetchCustomerPayments toggles the grid expansion closed when
                // called for the already-expanded id -- reload directly instead.
                const res = await apiService.fetchPayments(expandedCustomerId);
                setPayments(res.data?.payments || res.payments || []);
            }
        } catch (error) {
            console.error("Error collecting payment:", error);
            setSnackbar({ open: true, message: 'Failed to collect payment. ' + (error.response?.data?.message || error.message), severity: 'error' });
        } finally {
            setCollectSubmitting(false);
        }
    }, [collectDialog, collectSubmitting, apiService, setSnackbar, expandedCustomerId, paymentsModalCustomer, fetchCustomerPayments, payments, customers, waSettings]);

    // --- Customer-level receive / confirm (Payments History dialog) ---
    const isOpenBill = (p) => !p.paid && !p.pre_payment && !p.is_refund;
    const modalOwed = payments.filter(isOpenBill).reduce((t, p) => t + p.amount, 0);
    const modalOwedUncollected = payments.filter(p => isOpenBill(p) && !p.collected).reduce((t, p) => t + p.amount, 0);
    const modalCollectedPending = payments.filter(p => isOpenBill(p) && p.collected)
        .reduce((t, p) => t + (p.collected_amount ?? p.amount), 0);

    const closePaymentsModal = () => { setPaymentsModalCustomer(null); setPayments([]); setPaymentsModalTab(0); };

    const refreshPaymentsModal = () => {
        if (paymentsModalCustomer) fetchCustomerPayments(paymentsModalCustomer.id, paymentsModalCustomer);
        setStatementVersion(v => v + 1);
        refetchCustomers();
    };

    const openPaymentWhatsApp = (customer, amount, message) => {
        const phone = (customer?.phone || '').replace(/\D/g, '');
        if (!(waSettings.enabled && waSettings.mode === 'deeplink' && phone)) return false;
        const msg = (waSettings.deeplink_msg_payment || 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!')
            .replace('{customer_name}', customer.name || '')
            .replace('{amount}', amount.toFixed(2));
        const waUrl = `https://wa.me/${phone}?text=${encodeURIComponent(msg)}`;
        try { window.open(waUrl, '_blank', 'noopener,noreferrer'); } catch (e) { /* popup blocked: snackbar link below */ }
        setSnackbar({
            open: true, severity: 'success', message: `${message} — WhatsApp link ready`,
            action: (
                <Button color="inherit" size="small" onClick={() => window.open(waUrl, '_blank', 'noopener,noreferrer')} sx={{ fontWeight: 700, textDecoration: 'underline' }}>
                    Open WhatsApp
                </Button>
            )
        });
        return true;
    };

    const handleReceiveDone = (data, amount) => {
        setReceiveOpen(false);
        if (!openPaymentWhatsApp(paymentsModalCustomer, amount, data.message)) {
            setSnackbar({ open: true, message: data.message, severity: 'success' });
        }
        refreshPaymentsModal();
    };

    const handleConfirmCollected = async () => {
        if (!paymentsModalCustomer || confirmingCollected) return;
        setConfirmingCollected(true);
        try {
            const res = await apiService.confirmCollectedPayments(paymentsModalCustomer.id);
            setSnackbar({ open: true, message: res.data.message, severity: 'success' });
            refreshPaymentsModal();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.message || 'Failed to confirm.', severity: 'error' });
        } finally {
            setConfirmingCollected(false);
        }
    };

    const renderPaymentAction = (p) => {
        if (p.paid) return null;
        if (canManageSubscriptions) {
            return <Button size="small" variant="contained" color="success" onClick={() => handleMarkPaid(p.id, p.amount)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Mark Paid</Button>;
        }
        if (isCashierOnly) {
            return p.collected
                ? <Chip label="Collected – awaiting finance" size="small" color="warning" variant="outlined" />
                : <Button size="small" variant="contained" color="primary" onClick={() => handleCollectPayment(p.id, p.amount)} sx={{ borderRadius: '8px', textTransform: 'none', fontWeight: 600 }}>Collect</Button>;
        }
        return null;
    };

    const handleDeleteCustomer = useCallback(async (customerId) => {
        if (window.confirm('Are you sure you want to delete this customer? This action cannot be undone.')) {
            try {
                await apiService.deleteCustomer(customerId);
                setSnackbar({ open: true, message: 'Customer deleted successfully!', severity: 'success' });
                refetchCustomers();
            } catch (error) {
                console.error("Error deleting customer:", error);
                setSnackbar({ open: true, message: 'Failed to delete customer. ' + (error.response?.data?.error || error.message), severity: 'error' });
            }
        }
    }, [apiService, setSnackbar, refetchCustomers]);

    const handleSubscriptionAction = useCallback(async (action, customerId, confirmMessage) => {
        if (window.confirm(confirmMessage)) {
            try {
                const response = await action(customerId);
                refetchCustomers();

                const customer = (customers || []).find(c => c.id === customerId);
                const isRenewal = action === apiService.renewSubscription;

                if (isRenewal && waSettings.enabled && waSettings.mode === 'deeplink') {
                    const phone = (customer?.phone || '').replace(/\D/g, '');
                    const newExpiry = response?.data?.new_expiry_date || '';
                    if (phone) {
                        const msg = (waSettings.deeplink_msg_renewal || 'Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!')
                            .replace('{customer_name}', customer?.name || '')
                            .replace('{expiry_date}', newExpiry);
                        const waUrl = `https://wa.me/${phone}?text=${encodeURIComponent(msg)}`;

                        try {
                            window.open(waUrl, '_blank', 'noopener,noreferrer');
                        } catch (e) {
                            console.warn('Popup blocked, use snackbar link:', e);
                        }

                        setSnackbar({
                            open: true,
                            message: `Subscription renewed successfully! — WhatsApp link ready`,
                            severity: 'success',
                            action: (
                                <Button color="inherit" size="small" onClick={() => window.open(waUrl, '_blank', 'noopener,noreferrer')} sx={{ fontWeight: 700, textDecoration: 'underline' }}>
                                    Open WhatsApp
                                </Button>
                            )
                        });
                        return;
                    }
                }

                setSnackbar({ open: true, message: response?.data?.message || 'Action completed successfully!', severity: 'success' });
            } catch (error) {
                console.error(`Error with subscription action:`, error);
                setSnackbar({ open: true, message: `Failed to complete action. ${error.response?.data?.message || error.message}`, severity: 'error' });
            }
        }
    }, [apiService, setSnackbar, refetchCustomers, customers, waSettings]);
    const handleToggleWA = useCallback(async (customer) => {
        try {
            await apiService.updateCustomer(customer.id, {
                whatsapp_notifications_enabled: !customer.whatsapp_notifications_enabled
            });
            setSnackbar({ open: true, message: 'WhatsApp notifications preference updated', severity: 'success' });
            refetchCustomers();
        } catch (error) {
            console.error('Error toggling WA:', error);
            setSnackbar({ open: true, message: 'Failed to update preference', severity: 'error' });
        }
    }, [apiService, setSnackbar, refetchCustomers]);

    const handleSendWAReminder = useCallback((customerOrId) => {
        const customer = typeof customerOrId === 'object' ? customerOrId : customers.find(c => c.id === customerOrId);
        if (customer) {
            setWaReminderDialog({ open: true, customer });
            setWaReminderType('payment_reminder');
        }
    }, [customers]);

    const handleConfirmWAReminder = useCallback(async (templateType) => {
        if (!waReminderDialog.customer) return;
        const cust = waReminderDialog.customer;
        const custId = cust.id;
        setWaReminderDialog({ open: false, customer: null });
        try {
            if (waSettings.enabled && waSettings.mode === 'deeplink') {
                const phone = (cust.phone || '').replace(/\D/g, '');
                if (phone) {
                    const template = templateType === 'current_balance'
                        // eslint-disable-next-line no-template-curly-in-string
                        ? (waSettings.deeplink_msg_current_balance || 'Dear {customer_name}, your current balance is ${balance}. Expiry date: {expiry_date}. Thank you!')
                        // eslint-disable-next-line no-template-curly-in-string
                        : (waSettings.deeplink_msg_payment_reminder || 'Dear {customer_name}, this is a friendly reminder that your subscription payment is due. Balance: ${balance}. Thank you!');
                    const msg = template
                        .replaceAll('{customer_name}', cust.name || '')
                        .replaceAll('{balance}', parseFloat(cust.balance || 0).toFixed(2))
                        .replaceAll('{expiry_date}', cust.subscription_expiry_date || 'N/A');
                    const waUrl = `https://wa.me/${phone}?text=${encodeURIComponent(msg)}`;
                    try {
                        window.open(waUrl, '_blank', 'noopener,noreferrer');
                    } catch (e) {
                        console.warn('Popup blocked, use snackbar link:', e);
                    }
                    setSnackbar({
                        open: true,
                        message: `WhatsApp reminder link ready`,
                        severity: 'success',
                        action: (
                            <Button color="inherit" size="small" onClick={() => window.open(waUrl, '_blank', 'noopener,noreferrer')} sx={{ fontWeight: 700, textDecoration: 'underline' }}>
                                Open WhatsApp
                            </Button>
                        )
                    });
                    return;
                }
            }
            await apiService.sendWhatsappReminder(custId, templateType);
            setSnackbar({ open: true, message: `WhatsApp ${templateType.replace('_', ' ')} triggered!`, severity: 'success' });
        } catch (error) {
            console.error('Error sending WA reminder:', error);
            setSnackbar({ open: true, message: 'Failed to send WhatsApp reminder', severity: 'error' });
        }
    }, [waReminderDialog.customer, apiService, setSnackbar, waSettings]);

    // --- NEW: Bulk Action Handlers ---
    const handleExportCSV = async () => {
        try {
            setSnackbar({ open: true, message: 'Preparing export...', severity: 'info' });
            // Fetch all customers matching current filters (per_page=9999)
            const response = await apiService.fetchCustomers(1, 9999, debouncedSearchQuery, customerSortBy, customerResellerId, statusFilter, false, customerExpiryDay || '');
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

    // Fires ONE bulk request for the whole selection (instead of one HTTP call
    // per row) and guards against a double-click re-triggering the batch while
    // it's still in flight.
    const handleBulkAction = async (bulkApiCall, actionName, confirmMessage) => {
        if (bulkActionLoading) return;
        if (!window.confirm(confirmMessage)) return;

        setBulkActionLoading(true);
        try {
            const response = await bulkApiCall(selected);
            const { succeeded = [], failed = [] } = response.data;

            setSnackbar({
                open: true,
                message: `${actionName} successful for ${succeeded.length} subscription(s). ${failed.length > 0 ? `Failed for ${failed.length}.` : ''}`,
                severity: failed.length > 0 ? 'warning' : 'success'
            });

            setSelected([]); // Clear selection
            refetchCustomers(); // Refresh data
        } catch (error) {
            console.error(`Error performing bulk ${actionName}:`, error);
            setSnackbar({
                open: true,
                message: `Failed to ${actionName.toLowerCase()} selected subscriptions. ${error.response?.data?.message || error.message}`,
                severity: 'error'
            });
        } finally {
            setBulkActionLoading(false);
        }
    };

    const handleBulkRenew = () => {
        handleBulkAction(apiService.bulkRenewSubscriptions, 'Renew', `Renew ${selected.length} selected subscriptions? (Reseller customers will have their reseller charged, others will get new pending payments)`);
    };

    const handleBulkCancel = () => {
        handleBulkAction(apiService.bulkCancelSubscriptions, 'Cancel', `Cancel ${selected.length} selected subscriptions?`);
    };

    const handleBulkDelete = () => {
        handleBulkAction(apiService.bulkDeleteCustomers, 'Delete', `Are you sure you want to delete ${selected.length} selected customers? This action cannot be undone.`);
    };
    // --- End of NEW Bulk Action Handlers ---


    // Network Status panel (Concept B) -- fetch fresh whenever the edit dialog
    // opens for a Mikrotik-linked customer; never fires on its own otherwise.
    useEffect(() => {
        if (editDialogOpen && editingCustomer?.network_device_id && editingCustomer?.pppoe_username) {
            fetchNetworkStatus(editingCustomer.id);
        } else {
            // No poll owns the panel any more -- clearing the ref (not just
            // the state) stops a poll still resolving for whatever customer
            // was showing before from repainting over this null once it
            // lands. See activeNetworkStatusCustomerIdRef's declaration above.
            activeNetworkStatusCustomerIdRef.current = null;
            setMikrotikStatus(null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [editDialogOpen, editingCustomer?.id]);

    // Upstream status sync (Concept A) is manual-only -- a real headless-browser
    // check takes several seconds, unlike the instant Mikrotik API call above, so
    // it never auto-fires on dialog open. Seed from the customer's last persisted
    // sync (so the panel is "always visible" per spec, even before a fresh click)
    // instead of always nulling; clicking Refresh still triggers and displays a
    // live check exactly as before.
    useEffect(() => {
        if (editDialogOpen && editingCustomer && editingCustomer.upstream_last_synced_at) {
            setUpstreamSyncStatus({
                ok: true,
                upstream_last_status: editingCustomer.upstream_last_status,
                upstream_actual_expiry: editingCustomer.upstream_actual_expiry,
                upstream_last_synced_at: editingCustomer.upstream_last_synced_at,
                upstream_drift: editingCustomer.upstream_drift,
            });
        } else {
            setUpstreamSyncStatus(null);
        }
    }, [editDialogOpen, editingCustomer?.id]);

    const fetchUpstreamStatus = async (customerId) => {
        setUpstreamSyncLoading(true);
        try {
            const response = await apiService.syncCustomerUpstreamStatus(customerId);
            setUpstreamSyncStatus(response.data);
        } catch (error) {
            setUpstreamSyncStatus({ ok: false, error: error.response?.data?.error || error.response?.data?.message || 'Failed to sync status' });
        } finally {
            setUpstreamSyncLoading(false);
        }
    };

    const fetchNetworkStatus = async (customerId) => {
        const seq = (networkStatusSeqRef.current[customerId] || 0) + 1;
        networkStatusSeqRef.current[customerId] = seq;
        activeNetworkStatusCustomerIdRef.current = customerId;
        // True only while no newer poll (for this customer or another) and no
        // dialog-close/customer-switch has superseded this one -- see the
        // refs' declaration above.
        const isCurrent = () => activeNetworkStatusCustomerIdRef.current === customerId
            && networkStatusSeqRef.current[customerId] === seq;

        setMikrotikStatusLoading(true);
        try {
            const { data } = await apiService.fetchCustomerNetworkStatus(customerId);
            if (!data.ok) {
                // Complete shape (matching mergeNetworkStatus's), not just
                // secret_error -- an absent active_session/session_error
                // would otherwise render as though the session side were a
                // current "Not connected" rather than genuinely unknown.
                if (isCurrent()) setMikrotikStatus({ secret_status: null, secret_error: data.message, active_session: null, session_error: null, pending: false });
                return;
            }
            // Both jobs are already terminal in direct mode, so this usually
            // settles on the first pass. In agent mode the agent handles one
            // job per 2-second poll, so allow a few rounds.
            for (let attempt = 0; attempt < 15; attempt++) {
                const [secret, session] = await Promise.all([
                    apiService.fetchNetworkJob(data.jobs.secret),
                    apiService.fetchNetworkJob(data.jobs.session),
                ]);
                if (!isCurrent()) return;
                const merged = mergeNetworkStatus(secret.data, session.data);
                setMikrotikStatus(merged);
                if (!merged.pending) return;
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
            // All 15 attempts stayed pending -- land on a terminal shape
            // instead of leaving `pending: true` up forever, which rendered
            // as a permanent, spinner-less "Checking..." with no error and
            // no sign it gave up (mikrotikStatusLoading is already false by
            // then, since the loop above has returned control here).
            if (isCurrent()) {
                setMikrotikStatus({
                    secret_status: null,
                    secret_error: 'Status check timed out.',
                    active_session: null,
                    session_error: 'Status check timed out.',
                    pending: false,
                });
            }
        } catch (err) {
            if (isCurrent()) setMikrotikStatus({ secret_status: null, secret_error: err.response?.data?.error || 'Status check failed', active_session: null, session_error: null, pending: false });
        } finally {
            if (isCurrent()) setMikrotikStatusLoading(false);
        }
    };

    const handleNetworkAction = async (customerId, action) => {
        setMikrotikActionCustomerId(customerId);
        // The panel still belongs to this customer only while the ref does --
        // fetchNetworkStatus sets it, and the dialog reset nulls it. See
        // activeNetworkStatusCustomerIdRef's declaration.
        const isCurrent = () => activeNetworkStatusCustomerIdRef.current === customerId;
        try {
            const call = action === 'suspend' ? apiService.suspendCustomerNetwork : apiService.unsuspendCustomerNetwork;
            const { data } = await call(customerId);
            if (!data.ok) {
                // A refusal the backend chose to return as 200: an agent too
                // old to perform writes, or a job it declined to create. The
                // message says which.
                setSnackbar({ open: true, message: data.message, severity: 'warning' });
                return;
            }
            if (data.job_id) {
                // Agent mode: the router has not been touched yet. Wait for the
                // box to report, so the toast describes what actually happened
                // rather than that we asked.
                const job = await pollNetworkJob(data.job_id);
                setSnackbar({
                    open: true,
                    message: job.error || `Customer ${action}ed.`,
                    severity: job.error ? 'error' : 'success',
                });
            } else {
                setSnackbar({ open: true, message: data.message, severity: 'success' });
            }
            // Guarded, unlike the snackbar above: fetchNetworkStatus takes
            // ownership of the shared panel state, so calling it for a dialog
            // the user has since closed or replaced would repaint THIS
            // customer's chips into whichever customer is on screen now --
            // and re-enable the buttons beside them. The toast is deliberately
            // left unguarded: a global message about an action you took is
            // still worth seeing after you close the dialog.
            if (isCurrent()) await fetchNetworkStatus(customerId);
        } catch (err) {
            setSnackbar({
                open: true,
                message: err.response?.data?.message || `Failed to ${action} connection`,
                severity: 'error',
            });
        } finally {
            // Functional update: a newer action for a different customer may
            // have started while this one was polling, and clearing
            // unconditionally would unlock ITS buttons early.
            setMikrotikActionCustomerId(prev => (prev === customerId ? null : prev));
        }
    };

    // Opens the edit dialog for a customer, capturing the ONU MAC it started
    // with so handleUpdateCustomer can tell later whether the user actually
    // touched that field (see editingOnuMacSnapshotRef above).
    const openEditCustomerDialog = useCallback((customer) => {
        editingOnuMacSnapshotRef.current = customer.onu_mac_address || '';
        editingCpeMacSnapshotRef.current = customer.cpe_mac_address || '';
        setEditingCustomer(customer);
        setEditDialogOpen(true);
    }, []);

    const handleUpdateCustomer = useCallback(async () => {
        if (!editingCustomer) return;

        try {
            const payload = {
                name: editingCustomer.name,
                phone: editingCustomer.phone,
                address: editingCustomer.address,
                sector: editingCustomer.sector,
                subscription_plan_id: editingCustomer.subscription_plan_id,
                discount: editingCustomer.discount,
                cost_override: editingCustomer.cost_override,
                balance: editingCustomer.balance !== undefined ? editingCustomer.balance : 0,
                reseller_id: editingCustomer.reseller_id || "",
                upstream_provider_id: editingCustomer.upstream_provider_id || "",
                upstream_username: editingCustomer.upstream_username || "",
                network_device_id: editingCustomer.network_device_id || "",
                pppoe_username: editingCustomer.pppoe_username || "",
                notes: editingCustomer.notes || "",
            };
            if (isCashierOnly) delete payload.balance;

            // Only include onu_mac_address when the user actually changed it
            // in this dialog session. editingCustomer can be a stale snapshot
            // (see editingOnuMacSnapshotRef), and the backend treats an absent
            // key as "leave unchanged" vs. a present-but-empty key as "clear" --
            // so omitting an untouched field is both cheaper and strictly
            // correct, while an intentional clear still sends "".
            const currentOnuMac = editingCustomer.onu_mac_address || '';
            if (currentOnuMac !== editingOnuMacSnapshotRef.current) {
                payload.onu_mac_address = currentOnuMac;
            }

            // Same reasoning as onu_mac_address above, for the same reason:
            // only send cpe_mac_address when the user actually changed it in
            // this dialog session.
            const currentCpeMac = editingCustomer.cpe_mac_address || '';
            if (currentCpeMac !== editingCpeMacSnapshotRef.current) {
                payload.cpe_mac_address = currentCpeMac;
            }

            const response = await apiService.updateCustomer(editingCustomer.id, payload);

            setSnackbar({
                open: true,
                message: response.data.message || 'Customer updated successfully!',
                severity: 'success'
            });

            setEditDialogOpen(false);
            setEditingCustomer(null);
            refetchCustomers();

        } catch (error) {
            console.error('Error updating customer:', error);
            setSnackbar({
                open: true,
                message: 'Failed to update customer. ' + (error.response?.data?.error || error.message),
                severity: 'error'
            });
        }
    }, [editingCustomer, isCashierOnly, apiService, setSnackbar, refetchCustomers]);

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
            setNewCustomer({ name: '', phone: '', address: '', sector: '', subscription_plan_id: '', reseller_id: '', upstream_provider_id: '', upstream_username: '', network_device_id: '', pppoe_username: '', onu_mac_address: '', cpe_mac_address: '', discount: 0.0, cost_override: '', subscription_start_date: new Date().toISOString().split('T')[0], additional_payment_amount: 0.0, notes: '' });
            // Go to the first page after adding (the page change refetches).
            if (currentPage !== 1) setCurrentPage(1); else refetchCustomers();
        } catch (error) {
            console.error('Error adding customer:', error);
            setSnackbar({ open: true, message: 'Failed to add customer. ' + (error.response?.data?.error || error.message), severity: 'error' });
        }
    }, [newCustomer, apiService, setSnackbar, refetchCustomers, currentPage, setCurrentPage]);

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

    // Stable across renders so the memoized cards/rows don't re-render just
    // because the parent did; each method calls the latest handler.
    const hasWhatsApp = hasModule('whatsapp');
    const latestHandlersRef = React.useRef(null);
    latestHandlersRef.current = {
        fetchCustomerPayments, openEditCustomerDialog, handleSubscriptionAction,
        handleSendWAReminder, handleDeleteCustomer, handleToggleWA, handleSelectClick,
        renderUpstreamStatusChip, renderPaymentAction,
    };
    const cardActions = React.useMemo(() => {
        const h = () => latestHandlersRef.current;
        return {
            hasWhatsApp,
            fetchCustomerPayments: (...a) => h().fetchCustomerPayments(...a),
            openEditCustomerDialog: (...a) => h().openEditCustomerDialog(...a),
            handleSendWAReminder: (...a) => h().handleSendWAReminder(...a),
            handleDeleteCustomer: (...a) => h().handleDeleteCustomer(...a),
            handleToggleWA: (...a) => h().handleToggleWA(...a),
            handleSelectClick: (...a) => h().handleSelectClick(...a),
            renderUpstreamStatusChip: (customer) => h().renderUpstreamStatusChip(customer),
            renderPaymentAction: (p) => h().renderPaymentAction(p),
            renew: (id) => h().handleSubscriptionAction(apiService.renewSubscription, id, "Renew subscription? (Reseller customers will have their reseller charged, others will get a new pending payment)"),
            cancel: (id) => h().handleSubscriptionAction(apiService.cancelSubscription, id, "Cancel subscription?"),
            activate: (id) => h().handleSubscriptionAction(apiService.activateSubscription, id, "Activate subscription?"),
        };
    }, [apiService, hasWhatsApp]);
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
                {canServeAtDesk && (
                    <Button variant="contained" startIcon={<AddIcon />} onClick={() => setShowAddCustomerForm(true)} sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5 }}>
                        Add Customer
                    </Button>
                )}
            </Box>
        </Fade>
    );

    // Already filtered by status and ordered by the chosen Sort By on the
    // server -- re-sorting here used to override Sort By with expiry date.
    const sortedCustomers = customers;

    // Virtualized rendering (see useWindowRows): with "Items per page: all"
    // the page used to mount every card at once and freeze for seconds.
    // Grid view is virtualized by rows of cards, matching the old Grid
    // breakpoints (1 column, 2 from md, 3 from lg).
    const isLg = useMediaQuery(theme.breakpoints.up('lg'));
    const isMd = useMediaQuery(theme.breakpoints.up('md'));
    const gridColumns = isLg ? 3 : isMd ? 2 : 1;
    const gridRows = React.useMemo(() => chunkRows(sortedCustomers, gridColumns), [sortedCustomers, gridColumns]);
    const isGridView = viewMode === 'grid';
    const rowKey = useCallback((i) => (isGridView
        ? `g${gridColumns}:${gridRows[i]?.[0]?.id ?? i}`
        : `l:${sortedCustomers[i]?.id ?? i}`), [isGridView, gridColumns, gridRows, sortedCustomers]);
    const windowRows = useWindowRows(isGridView ? gridRows.length : sortedCustomers.length, isGridView ? 420 : 88, rowKey);

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
                        {canServeAtDesk && (
                            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', sm: 'row' }, gap: 2 }}>
                                {canManageSubscriptions && <Button variant="contained" startIcon={<DownloadIcon />} onClick={handleExportCSV} sx={{ backgroundColor: 'rgba(255, 255, 255, 0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255, 255, 255, 0.3)', color: 'white', borderRadius: '16px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5, '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' }, transition: 'all 0.3s ease' }}>
                                    Export CSV
                                </Button>}
                                {isAdmin && <Button variant="contained" startIcon={<UploadFileIcon />} onClick={() => setImportOpen(true)} sx={{ backgroundColor: 'rgba(255, 255, 255, 0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255, 255, 255, 0.3)', color: 'white', borderRadius: '16px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5, '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' }, transition: 'all 0.3s ease' }}>
                                    Import
                                </Button>}
                                <Button variant="contained" startIcon={<AddIcon />} onClick={() => setShowAddCustomerForm(!showAddCustomerForm)} sx={{ backgroundColor: 'rgba(255, 255, 255, 0.2)', backdropFilter: 'blur(10px)', border: '1px solid rgba(255, 255, 255, 0.3)', color: 'white', borderRadius: '16px', textTransform: 'none', fontWeight: 600, px: 3, py: 1.5, '&:hover': { backgroundColor: 'rgba(255, 255, 255, 0.3)', transform: 'translateY(-2px)', boxShadow: '0 8px 20px rgba(0,0,0,0.2)' }, transition: 'all 0.3s ease' }}>
                                    {showAddCustomerForm ? 'Hide Form' : 'Add Customer'}
                                </Button>
                            </Box>
                        )}
                    </Box>
                    {canManageSubscriptions && (
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
                    )}
                </Box>
            </Paper>

            <Collapse in={showAddCustomerForm} unmountOnExit>
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
                        {businessSettings?.network_mode === 'upstream_bridge' && (
                            <>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth select label="Upstream Provider (Optional)" value={newCustomer.upstream_provider_id || ''} onChange={(e) => setNewCustomer({ ...newCustomer, upstream_provider_id: e.target.value })}>
                                        <MenuItem value="">None</MenuItem>
                                        {upstreamProviders.map(p => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
                                    </TextField>
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth label="Upstream Username (Optional)" value={newCustomer.upstream_username || ''} onChange={(e) => setNewCustomer({ ...newCustomer, upstream_username: e.target.value })} />
                                </Grid>
                            </>
                        )}
                        {businessSettings?.network_mode === 'local_mikrotik' && (
                            <>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth select label="Router (Optional)" value={newCustomer.network_device_id || ''} onChange={(e) => setNewCustomer({ ...newCustomer, network_device_id: e.target.value })}>
                                        <MenuItem value="">None</MenuItem>
                                        {networkDevices.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
                                    </TextField>
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth label="PPPoE Username (Optional)" value={newCustomer.pppoe_username || ''} onChange={(e) => setNewCustomer({ ...newCustomer, pppoe_username: e.target.value })} />
                                </Grid>
                            </>
                        )}
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth label="ONU MAC Address (Optional)" value={newCustomer.onu_mac_address || ''}
                                onChange={(e) => setNewCustomer({ ...newCustomer, onu_mac_address: e.target.value })}
                                helperText="Links this customer to the ONU serving them on the Network Tree. Leave blank if unknown; clearing it later unlinks the customer from their ONU." />
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth label="CPE MAC Address (router, optional)"
                                value={newCustomer.cpe_mac_address || ''}
                                onChange={(e) => setNewCustomer({ ...newCustomer, cpe_mac_address: e.target.value })}
                                helperText="The customer's own router, as the OLT sees it. Used to place them on the network map." />
                        </Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount (Fixed Amount)" value={newCustomer.discount} onChange={(e) => setNewCustomer({ ...newCustomer, discount: parseFloat(e.target.value) || 0.0 })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Cost Override (Optional)" value={newCustomer.cost_override} onChange={(e) => setNewCustomer({ ...newCustomer, cost_override: e.target.value })} helperText="Leave blank to use the plan's default cost" /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="date" label="Subscription Start Date" value={newCustomer.subscription_start_date} onChange={(e) => setNewCustomer({ ...newCustomer, subscription_start_date: e.target.value })} InputLabelProps={{ shrink: true }} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Additional Payment Amount" value={newCustomer.additional_payment_amount} onChange={(e) => setNewCustomer({ ...newCustomer, additional_payment_amount: parseFloat(e.target.value) || 0.0 })} helperText="For one-time charges on creation" /></Grid>
                        <Grid item xs={12}><TextField fullWidth multiline minRows={2} label="Notes (Optional)" value={newCustomer.notes || ''} onChange={(e) => setNewCustomer({ ...newCustomer, notes: e.target.value })} inputProps={{ maxLength: 2000 }} helperText="Shown on the subscription card" /></Grid>
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
                    <TextField select label="Status" value={statusFilter} onChange={(e) => { setCustomerStatus(e.target.value); setCurrentPage(1); }} sx={{ minWidth: 150 }}>
                        <MenuItem value="active">Active</MenuItem>
                        <MenuItem value="canceled">Canceled</MenuItem>
                        <MenuItem value="all">All</MenuItem>
                    </TextField>
                    <TextField select label="Reseller" value={customerResellerId || ''} onChange={(e) => setCustomerResellerId(e.target.value)} sx={{ minWidth: 150 }}>
                        <MenuItem value="">All Resellers</MenuItem>
                        {resellers && resellers.map(r => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
                    </TextField>
                    {/* Day of the month the subscription ends on -- e.g. everyone due on the 1st. Filtered on the server. */}
                    <TextField select label="Expiry Day" value={customerExpiryDay || ''} onChange={(e) => { setCustomerExpiryDay(e.target.value); setCurrentPage(1); }} sx={{ minWidth: 130 }}
                        SelectProps={{ MenuProps: { PaperProps: { sx: { maxHeight: 320 } } } }}>
                        <MenuItem value="">Any day</MenuItem>
                        {EXPIRY_DAYS.map(d => <MenuItem key={d} value={d}>Day {d}</MenuItem>)}
                    </TextField>
                    <TextField select label="Sort By" value={customerSortBy || 'expiry_date'} onChange={(e) => setCustomerSortBy(e.target.value)} sx={{ minWidth: 150 }}>
                        <MenuItem value="expiry_date">Expiry Date</MenuItem>
                        <MenuItem value="name">Name</MenuItem>
                        <MenuItem value="address">Address</MenuItem>
                    </TextField>
                    <TextField select label="Items per page" value={itemsPerPage} onChange={(e) => { setItemsPerPage(Number(e.target.value)); setCurrentPage(1); }} sx={{ minWidth: 120 }}>
                        <MenuItem value={10}>10</MenuItem><MenuItem value={25}>25</MenuItem><MenuItem value={50}>50</MenuItem><MenuItem value={100}>100</MenuItem><MenuItem value={100000}>all</MenuItem>
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
                <div ref={windowRows.anchorRef}>
                    <div style={{ height: windowRows.padTop }} />
                    {windowRows.items.map((vr) => (
                        <Box
                            key={vr.key}
                            data-index={vr.index}
                            ref={windowRows.virtualizer.measureElement}
                            sx={{ display: 'grid', gridTemplateColumns: `repeat(${gridColumns}, minmax(0, 1fr))`, gap: `${GRID_ROW_GAP_PX}px`, pb: `${GRID_ROW_GAP_PX}px`, alignItems: 'start' }}
                        >
                            {(gridRows[vr.index] || []).map((customer) => {
                        const isExpanded = expandedCustomerId === customer.id;
                        return (
                            <GridCustomerCard
                                key={customer.id}
                                customer={customer}
                                isExpanded={isExpanded}
                                payments={isExpanded ? payments : NO_PAYMENTS}
                                loadingPayments={isExpanded && loadingPayments}
                                isSyncing={syncingCustomerIds.has(customer.id)}
                                networkMode={businessSettings?.network_mode}
                                canServeAtDesk={canServeAtDesk}
                                canManageSubscriptions={canManageSubscriptions}
                                actions={cardActions}
                            />
                        );
                            })}
                        </Box>
                    ))}
                    <div style={{ height: windowRows.padBottom }} />
                </div>
            ) : (
                // --- LIST VIEW (New) ---
                <Paper sx={{ width: '100%', mb: 2, borderRadius: '16px', overflow: 'hidden' }}>
                    {canServeAtDesk && (
                        <EnhancedTableToolbar
                            numSelected={selected.length}
                            onRenew={handleBulkRenew}
                            onCancel={handleBulkCancel}
                            onDelete={canManageSubscriptions ? handleBulkDelete : undefined}
                            disabled={bulkActionLoading}
                        />
                    )}
                    <TableContainer>
                        <Table sx={{ minWidth: 750 }} aria-labelledby="tableTitle">
                            <TableHead sx={{ backgroundColor: alpha(theme.palette.primary.main, 0.05) }}>
                                <TableRow>
                                    {canServeAtDesk && (
                                        <TableCell padding="checkbox">
                                            <Checkbox
                                                color="primary"
                                                indeterminate={selected.length > 0 && selected.length < sortedCustomers.length}
                                                checked={sortedCustomers.length > 0 && selected.length === sortedCustomers.length}
                                                onChange={handleSelectAllClick}
                                                inputProps={{ 'aria-label': 'select all customers' }}
                                            />
                                        </TableCell>
                                    )}
                                    <TableCell sx={{ fontWeight: 700 }}>Customer</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Contact</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Plan</TableCell>
                                    <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                                    {canServeAtDesk && <TableCell sx={{ fontWeight: 700 }}>WA Alerts</TableCell>}
                                    {canServeAtDesk && <TableCell sx={{ fontWeight: 700 }}>Balance</TableCell>}
                                    <TableCell sx={{ fontWeight: 700 }}>Expiry Date</TableCell>
                                    {canServeAtDesk && <TableCell sx={{ fontWeight: 700 }}>Actions</TableCell>}
                                </TableRow>
                            </TableHead>
                            <TableBody ref={windowRows.anchorRef}>
                                {windowRows.padTop > 0 && (
                                    <tr aria-hidden="true" style={{ height: windowRows.padTop }}><td colSpan={10} style={{ padding: 0, border: 0 }} /></tr>
                                )}
                                {windowRows.items.map((vr) => {
                                    const customer = sortedCustomers[vr.index];
                                    if (!customer) return null;
                                    return (
                                    <ListCustomerRow
                                        key={customer.id}
                                        customer={customer}
                                        index={vr.index}
                                        measureRef={windowRows.virtualizer.measureElement}
                                        isItemSelected={selected.indexOf(customer.id) !== -1}
                                        isSyncing={syncingCustomerIds.has(customer.id)}
                                        networkMode={businessSettings?.network_mode}
                                        canServeAtDesk={canServeAtDesk}
                                        canManageSubscriptions={canManageSubscriptions}
                                        actions={cardActions}
                                    />
                                    );
                                })}
                                {windowRows.padBottom > 0 && (
                                    <tr aria-hidden="true" style={{ height: windowRows.padBottom }}><td colSpan={10} style={{ padding: 0, border: 0 }} /></tr>
                                )}
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
                        {businessSettings?.network_mode === 'upstream_bridge' && (
                            <>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth select label="Upstream Provider (Optional)" value={editingCustomer?.upstream_provider_id || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, upstream_provider_id: e.target.value })}>
                                        <MenuItem value="">None</MenuItem>
                                        {upstreamProviders.map(p => <MenuItem key={p.id} value={p.id}>{p.name}</MenuItem>)}
                                    </TextField>
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth label="Upstream Username (Optional)" value={editingCustomer?.upstream_username || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, upstream_username: e.target.value })} />
                                </Grid>
                            </>
                        )}
                        {businessSettings?.network_mode === 'local_mikrotik' && (
                            <>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth select label="Router (Optional)" value={editingCustomer?.network_device_id || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, network_device_id: e.target.value })}>
                                        <MenuItem value="">None</MenuItem>
                                        {networkDevices.map(d => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
                                    </TextField>
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <TextField fullWidth label="PPPoE Username (Optional)" value={editingCustomer?.pppoe_username || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, pppoe_username: e.target.value })} />
                                </Grid>
                            </>
                        )}
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth label="ONU MAC Address (Optional)" value={editingCustomer?.onu_mac_address || ''}
                                onChange={(e) => setEditingCustomer({ ...editingCustomer, onu_mac_address: e.target.value })}
                                helperText="Links this customer to the ONU serving them on the Network Tree. Clearing this unlinks the customer from their ONU." />
                        </Grid>
                        <Grid item xs={12} md={6}>
                            <TextField fullWidth label="CPE MAC Address (router, optional)"
                                value={editingCustomer?.cpe_mac_address || ''}
                                onChange={(e) => setEditingCustomer({ ...editingCustomer, cpe_mac_address: e.target.value })}
                                helperText="The customer's own router, as the OLT sees it. Used to place them on the network map." />
                        </Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Discount ($)" value={editingCustomer?.discount || 0} onChange={(e) => setEditingCustomer({ ...editingCustomer, discount: parseFloat(e.target.value) || 0 })} /></Grid>
                        <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Cost Override (Optional)" value={editingCustomer?.cost_override ?? ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, cost_override: e.target.value })} helperText="Leave blank to use the plan's default cost" /></Grid>
                        <Grid item xs={12}><TextField fullWidth multiline minRows={2} label="Notes (Optional)" value={editingCustomer?.notes || ''} onChange={(e) => setEditingCustomer({ ...editingCustomer, notes: e.target.value })} inputProps={{ maxLength: 2000 }} helperText="Shown on the subscription card" /></Grid>
                        {!isCashierOnly && <Grid item xs={12} md={6}><TextField fullWidth type="number" label="Account Balance ($)" value={editingCustomer?.balance !== undefined ? editingCustomer.balance : 0} helperText="Negative value = Customer owes money. 0 = Paid." onChange={(e) => setEditingCustomer({ ...editingCustomer, balance: parseFloat(e.target.value) || 0 })} /></Grid>}

                        {!isCashierOnly && hasModule('network') && businessSettings?.network_mode === 'local_mikrotik' && editingCustomer?.network_device_id && editingCustomer?.pppoe_username && (
                            <Grid item xs={12}>
                                <Box sx={{ p: 2, borderRadius: '12px', border: `1px solid ${alpha(theme.palette.divider, 0.15)}`, bgcolor: '#f8fafc' }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Network Status (Mikrotik)</Typography>
                                        <Button size="small" onClick={() => fetchNetworkStatus(editingCustomer.id)} disabled={mikrotikStatusLoading}>
                                            {mikrotikStatusLoading ? <CircularProgress size={16} /> : 'Refresh'}
                                        </Button>
                                    </Box>
                                    {shouldShowNetworkStatusChips(mikrotikStatus, hasModule('network')) ? (
                                        <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
                                            <Chip
                                                size="small"
                                                label={mikrotikStatus.secret_error ? `Error: ${mikrotikStatus.secret_error}` : `Secret: ${mikrotikStatus.secret_status || 'unknown'}`}
                                                color={mikrotikStatus.secret_status === 'enabled' ? 'success' : mikrotikStatus.secret_status === 'disabled' ? 'error' : 'default'}
                                            />
                                            <Chip
                                                size="small"
                                                variant="outlined"
                                                label={mikrotikStatus.active_session ? 'Currently connected' : 'Not connected'}
                                            />
                                            <Button size="small" variant="outlined" color="error" disabled={mikrotikActionCustomerId === editingCustomer?.id}
                                                onClick={() => handleNetworkAction(editingCustomer.id, 'suspend')}>
                                                Suspend
                                            </Button>
                                            <Button size="small" variant="outlined" color="success" disabled={mikrotikActionCustomerId === editingCustomer?.id}
                                                onClick={() => handleNetworkAction(editingCustomer.id, 'unsuspend')}>
                                                Unsuspend
                                            </Button>
                                        </Box>
                                    ) : (
                                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                                            {mikrotikStatusLoading || mikrotikStatus?.pending ? 'Checking…' : 'No status loaded yet.'}
                                        </Typography>
                                    )}
                                </Box>
                            </Grid>
                        )}
                        {!isCashierOnly && hasModule('upstream_sync') && businessSettings?.network_mode === 'upstream_bridge' && editingCustomer?.upstream_provider_id && editingCustomer?.upstream_username && (
                            <Grid item xs={12}>
                                <Box sx={{ p: 2, borderRadius: '12px', border: `1px solid ${alpha(theme.palette.divider, 0.15)}`, bgcolor: '#f8fafc' }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                                        <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Network Status (Upstream Portal)</Typography>
                                        <Button size="small" onClick={() => fetchUpstreamStatus(editingCustomer.id)} disabled={upstreamSyncLoading}>
                                            {upstreamSyncLoading ? <CircularProgress size={16} /> : 'Refresh Upstream Status'}
                                        </Button>
                                    </Box>
                                    {upstreamSyncStatus ? (
                                        upstreamSyncStatus.ok === false ? (
                                            <Typography variant="body2" color="error" sx={{ mt: 1 }}>
                                                {(upstreamSyncStatus.error && (UPSTREAM_SYNC_ERROR_MESSAGES[upstreamSyncStatus.error] || upstreamSyncStatus.error)) || 'Sync failed'}
                                            </Typography>
                                        ) : (
                                            <Box sx={{ mt: 1, display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
                                                <Chip
                                                    size="small"
                                                    label={`Portal status: ${getUpstreamStatusLabel(upstreamSyncStatus.upstream_last_status)}`}
                                                    sx={(() => {
                                                        const color = getUpstreamStatusColor(upstreamSyncStatus.upstream_last_status);
                                                        return { backgroundColor: alpha(color, 0.1), color, fontWeight: 600, border: `1px solid ${alpha(color, 0.2)}` };
                                                    })()}
                                                />
                                                <Chip
                                                    size="small"
                                                    variant="outlined"
                                                    label={`As of ${upstreamSyncStatus.upstream_last_synced_at
                                                        ? formatStamp(upstreamSyncStatus.upstream_last_synced_at)
                                                        : 'now'}`}
                                                />
                                                {upstreamSyncStatus.upstream_drift && (
                                                    <Chip
                                                        size="small"
                                                        color={upstreamSyncStatus.upstream_drift.severity === 'alert' ? 'error' : 'info'}
                                                        label={
                                                            upstreamSyncStatus.upstream_drift.severity === 'alert'
                                                                ? `⚠ Upstream expires ${upstreamSyncStatus.upstream_drift.days} day(s) before ServiceBills`
                                                                : `Upstream has ${upstreamSyncStatus.upstream_drift.days} extra day(s)`
                                                        }
                                                    />
                                                )}
                                            </Box>
                                        )
                                    ) : (
                                        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                                            {upstreamSyncLoading ? 'Checking…' : 'No status loaded yet.'}
                                        </Typography>
                                    )}
                                </Box>
                            </Grid>
                        )}
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setEditDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleUpdateCustomer}>Save Changes</Button>
                </DialogActions>
            </Dialog>

            <Dialog open={Boolean(paymentsModalCustomer)} onClose={closePaymentsModal} maxWidth="md" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Payments History - {paymentsModalCustomer?.name}</DialogTitle>
                <Box sx={{ px: 3, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 1 }}>
                    <Typography variant="body2" color="text.secondary">
                        Owed: <strong>${modalOwed.toFixed(2)}</strong>
                        {modalCollectedPending > 0 && <> · Collected, awaiting confirmation: <strong>${modalCollectedPending.toFixed(2)}</strong></>}
                    </Typography>
                    <Box sx={{ display: 'flex', gap: 1 }}>
                        {canManageSubscriptions && modalCollectedPending > 0 && (
                            <Button size="small" variant="outlined" color="success" onClick={handleConfirmCollected} disabled={confirmingCollected}
                                startIcon={confirmingCollected ? <CircularProgress size={14} color="inherit" /> : <CheckCircleIcon />}>
                                Confirm collected
                            </Button>
                        )}
                        {(canManageSubscriptions || (isCashierOnly && modalOwedUncollected > 0)) && (
                            <Button size="small" variant="contained" color="success" onClick={() => setReceiveOpen(true)}>
                                {canManageSubscriptions ? 'Receive payment' : 'Collect payment'}
                            </Button>
                        )}
                    </Box>
                </Box>
                <Tabs value={paymentsModalTab} onChange={(e, v) => setPaymentsModalTab(v)} sx={{ px: 3, borderBottom: 1, borderColor: 'divider' }}>
                    <Tab label="Bills" />
                    <Tab label="Statement" />
                </Tabs>
                <DialogContent>
                    {paymentsModalTab === 1 ? (
                        paymentsModalCustomer && (
                            <BalanceLogTable key={`${paymentsModalCustomer.id}:${statementVersion}`} increaseIsGood
                                load={() => apiService.getCustomerBalanceLog(paymentsModalCustomer.id)} />
                        )
                    ) : loadingPayments ? (
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
                                            <TableCell>{renderPaymentStatusChip(p)}</TableCell>
                                            <TableCell>{renderPaymentAction(p)}</TableCell>
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
                    <Button onClick={closePaymentsModal}>Close</Button>
                </DialogActions>
            </Dialog>

            <ReceivePaymentDialog
                open={receiveOpen}
                onClose={() => setReceiveOpen(false)}
                onDone={handleReceiveDone}
                customer={paymentsModalCustomer}
                owed={canManageSubscriptions ? modalOwed : modalOwedUncollected}
                mode={canManageSubscriptions ? 'pay' : 'collect'}
            />

            {/* Cashier: collect a payment */}
            <Dialog open={collectDialog.open} onClose={() => !collectSubmitting && closeCollectDialog()} maxWidth="xs" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Collect Payment</DialogTitle>
                <DialogContent>
                    <Typography variant="body2" sx={{ mb: 2, color: 'text.secondary' }}>
                        Outstanding: <strong>${(collectDialog.outstanding || 0).toFixed(2)}</strong>
                    </Typography>
                    <TextField fullWidth autoFocus type="number" label="Amount collected" value={collectDialog.amount}
                        onChange={(e) => setCollectDialog({ ...collectDialog, amount: e.target.value })}
                        InputProps={{ inputProps: { min: 0.01, step: 0.01 } }}
                        helperText={parseFloat(collectDialog.amount) < collectDialog.outstanding ? 'Partial payment' : 'Full payment'} />
                    <TextField select fullWidth sx={{ mt: 2 }} label="Paid by" value={collectDialog.method}
                        onChange={(e) => setCollectDialog({ ...collectDialog, method: e.target.value })}>
                        <MenuItem value="cash">Cash</MenuItem>
                        <MenuItem value="whish_transfer">Whish transfer (sent directly to our Whish account)</MenuItem>
                    </TextField>
                    {collectDialog.method === 'whish_transfer' && (
                        <TextField fullWidth sx={{ mt: 2 }} label="Whish reference (optional)" value={collectDialog.reference}
                            inputProps={{ maxLength: 64 }} onChange={(e) => setCollectDialog({ ...collectDialog, reference: e.target.value })}
                            helperText="Not counted as cash on the Daily Cash report" />
                    )}
                </DialogContent>
                <DialogActions>
                    <Button onClick={closeCollectDialog} disabled={collectSubmitting}>Cancel</Button>
                    <Button variant="contained" onClick={submitCollectPayment} disabled={collectSubmitting}
                        startIcon={collectSubmitting ? <CircularProgress size={16} color="inherit" /> : null}>
                        Collect
                    </Button>
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
            <CustomerImportWizard open={importOpen} onClose={() => setImportOpen(false)} onImported={() => { if (currentPage !== 1) setCurrentPage(1); else refetchCustomers(); }} />
        </Box>
    );
};

export default SubscriptionsView;
