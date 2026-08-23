import React, { useState, useEffect, useCallback } from 'react';
import {
    Button, AppBar, Toolbar, Typography, Box, CircularProgress,
    Snackbar, Alert, IconButton, Drawer, List, ListItem, ListItemButton,
    ListItemIcon, ListItemText, Divider, alpha, useTheme, useMediaQuery, Chip,
    ThemeProvider, CssBaseline, Fab, Zoom, useScrollTrigger
} from '@mui/material';
import { BrowserRouter, useLocation } from 'react-router-dom';
import theme from './theme';
import {
    Menu as MenuIcon,
    Close as CloseIcon,
    Dashboard as DashboardIcon,
    People as PeopleIcon,
    Payment as PaymentIcon,
    Receipt as ReceiptIcon,
    AccountBalance as ExpenseIcon,
    BarChart as ReportIcon,
    Build as ServiceIcon,
    TrendingUp as EnhancedReportIcon,
    Subscriptions as PlansIcon,
    Settings as SettingsIcon,
    ShoppingCart as ShoppingCartIcon,
    Logout as LogoutIcon,
    ChevronRight as ChevronRightIcon,
    Campaign as MessageIcon,
    Storefront as ResellerIcon,
    Badge as PayrollIcon,
    KeyboardArrowUp as KeyboardArrowUpIcon,
    CloudQueue as UpstreamProviderIcon,
    Router as MikrotikIcon,
} from '@mui/icons-material';
import { AppContextProvider, useAppContext, apiService } from './context/AppContext.js';
import DashboardView from './components/DashboardView.js';
import SubscriptionsView from './components/SubscriptionsView.js';
import PaymentsView from './components/PaymentsView.js';
import ExpensesView from './components/ExpensesView.js';
import ReportsView from './components/ReportsView.js';
import SettingsView from './components/SettingsView.js';
import SubscriptionPlansView from './components/SubscriptionPlansView.js';
import ReceiptsView from './components/ReceiptsView.js';
import LoginView from './components/LoginView.js';
import RegisterView from './components/RegisterView.js';
import VerifyEmailView from './components/VerifyEmailView.js';
import ForgotPasswordView from './components/ForgotPasswordView.js';
import ResetPasswordView from './components/ResetPasswordView.js';
import BillingView from './components/BillingView.js';
import SuperAdminView from './components/SuperAdminView.js';
import LandingView from './components/LandingView.js';
import ServiceManagementView from './components/ServiceManagementView.js';
import EnhancedReportsView from './components/EnhancedReportsView.js';
import MessagingView from './components/MessagingView.js';
import ResellerManagementView from './components/ResellerManagementView.js';
import SuppliersView from './components/SuppliersView.js';
import EmployeesView from './components/EmployeesView.js';
import UpstreamProviderManagementView from './components/UpstreamProviderManagementView.js';
import MikrotikServerManagementView from './components/MikrotikServerManagementView.js';

// ── Navigation config ────────────────────────────────────────────────────────
const NAV_ITEMS = [
    { key: 'dashboard',          label: 'Dashboard',          icon: <DashboardIcon />,      group: 'main',      allowedRoles: ['admin', 'finance'] },
    // 'employee' gets a read-only view here (status only, no balance/actions)
    // -- enforced inside SubscriptionsView.js, not by this nav entry.
    { key: 'subscriptions',      label: 'Subscriptions',      icon: <PeopleIcon />,          group: 'main',      allowedRoles: ['admin', 'finance', 'employee'] },
    { key: 'resellers',          label: 'Resellers',          icon: <ResellerIcon />,        group: 'main',      allowedRoles: ['admin', 'finance'] },
    { key: 'suppliers',          label: 'Suppliers',          icon: <ShoppingCartIcon />,        group: 'main',      allowedRoles: ['admin', 'finance'] },
    // Concept A/B (see docs/superpowers/specs/2026-08-12-network-enforcement-design.md):
    // only one of these is ever relevant, gated by BusinessSettings.network_mode, not roles.
    { key: 'upstream-providers',  label: 'Upstream Providers',  icon: <UpstreamProviderIcon />, group: 'main',    allowedRoles: ['admin', 'finance'], visibleWhen: (bs) => bs?.network_mode === 'upstream_bridge' },
    { key: 'mikrotik-servers',    label: 'Mikrotik Servers',    icon: <MikrotikIcon />,         group: 'main',    allowedRoles: ['admin', 'finance'], visibleWhen: (bs) => bs?.network_mode === 'local_mikrotik' },
    { key: 'employees',          label: 'Payroll',            icon: <PayrollIcon />,             group: 'main',      allowedRoles: ['admin'] },
    { key: 'payments',           label: 'Payments',           icon: <PaymentIcon />,         group: 'main',      allowedRoles: ['admin', 'finance', 'collector'] },
    { key: 'receipts',           label: 'Receipts',           icon: <ReceiptIcon />,         group: 'main',      allowedRoles: ['admin', 'finance'] },
    { key: 'expenses',           label: 'Expenses',           icon: <ExpenseIcon />,         group: 'main',      allowedRoles: ['admin'] },
    { key: 'reports',            label: 'Reports',            icon: <ReportIcon />,          group: 'analytics', allowedRoles: ['admin'] },
    { key: 'enhanced-reports',   label: 'Enhanced Reports',   icon: <EnhancedReportIcon />,  group: 'analytics', allowedRoles: ['admin'] },
    { key: 'service',            label: 'Service Management', icon: <ServiceIcon />,         group: 'manage',    allowedRoles: ['admin', 'employee', 'technician'] },
    { key: 'subscription-plans', label: 'Subscription Plans', icon: <PlansIcon />,           group: 'manage',    allowedRoles: ['admin', 'finance'] },
    { key: 'messaging',          label: 'Messaging',          icon: <MessageIcon />,         group: 'manage',    allowedRoles: ['admin'] },
    { key: 'settings',           label: 'Settings',           icon: <SettingsIcon />,        group: 'manage',    allowedRoles: ['admin'] },
    { key: 'billing',            label: 'Billing & Plan',     icon: <PaymentIcon />,         group: 'manage',    allowedRoles: ['admin'] },
];

const GROUP_LABELS = { main: 'Main', analytics: 'Analytics', manage: 'Management' };

// The app-wide default logo (frontend/public/serviceBillsLogo.png) is a static
// frontend asset, not something the API serves — resolve it relative to this
// origin even in dev, unlike tenant-uploaded logos which live behind the API.
const DEFAULT_LOGO_PATH = '/serviceBillsLogo.png';
const resolveLogoUrl = (logoUrl) => {
    if (!logoUrl) return null;
    if (logoUrl.startsWith('http') || logoUrl === DEFAULT_LOGO_PATH) return logoUrl;
    const apiBase = process.env.REACT_APP_API_URL ?? (process.env.NODE_ENV === 'production' ? '' : 'http://localhost:5000');
    return `${apiBase}${logoUrl}`;
};

// ── Drawer width ─────────────────────────────────────────────────────────────
const DRAWER_WIDTH = 280;

// ── MainApp ──────────────────────────────────────────────────────────────────
const MainApp = ({
    customers, pagination, subscriptionPlans, businessSettings,
    refetchCustomers, refetchSubscriptionPlans, setSnackbar, setBusinessSettings,
    currentPage, setCurrentPage, itemsPerPage, setItemsPerPage,
    searchQuery, setSearchQuery, customerSortBy, setCustomerSortBy,
    customerResellerId, setCustomerResellerId
}) => {
    const { user, logout } = useAppContext();
    const theme = useTheme();
    const isMobile = useMediaQuery(theme.breakpoints.down('md'));
    const userRoles = (user?.role || '').split(',').map(r => r.trim());
    const hasRole = (role) => userRoles.includes(role);

    // Floating "back to top" button (mobile only) -- the AppBar/hamburger is
    // sticky, but on long pages scrolling back up to it can still take a
    // while, so surface a shortcut once the user has scrolled past a screen.
    const scrollTrigger = useScrollTrigger({ target: window, disableHysteresis: true, threshold: 300 });
    const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });

    const getDefaultView = () => {
        if (window.location.pathname.startsWith('/billing')) return 'billing';
        const urlParams = new URLSearchParams(window.location.search);
        const viewParam = urlParams.get('view');
        if (viewParam) return viewParam;

        if (hasRole('admin') || hasRole('finance')) return 'dashboard';
        if (hasRole('employee') || hasRole('technician')) return 'service';
        if (hasRole('collector')) return 'payments';
        return 'dashboard';
    };
    const [currentView, setCurrentView] = useState(getDefaultView());
    const [drawerOpen, setDrawerOpen] = useState(false);

    const navigate = (key) => {
        setCurrentView(key);
        setDrawerOpen(false);
    };

    const navItems = NAV_ITEMS.filter(item =>
        (!item.allowedRoles || item.allowedRoles.some(r => hasRole(r))) &&
        (!item.visibleWhen || item.visibleWhen(businessSettings))
    );
    const currentLabel = navItems.find(n => n.key === currentView)?.label || 'Dashboard';

    // Per-tenant branding: resolve the logo (custom upload or app default) to an absolute URL.
    const logoSrc = resolveLogoUrl(businessSettings?.logo_url) || DEFAULT_LOGO_PATH;
    const brandName = businessSettings?.business_name || 'servicesBills';

    // ── Desktop horizontal nav ────────────────────────────────────────────────
    const DesktopNav = () => (
        <Box sx={{ display: { xs: 'none', md: 'flex' }, gap: 0.5, px: 1, py: 1, flexWrap: 'wrap', justifyContent: 'center', bgcolor: 'background.paper', borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`, boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
            {navItems.map(item => (
                <Button key={item.key}
                    variant={currentView === item.key ? 'contained' : 'text'}
                    startIcon={React.cloneElement(item.icon, { sx: { fontSize: '1rem' } })}
                    onClick={() => navigate(item.key)}
                    size="small"
                    sx={{
                        borderRadius: '10px', textTransform: 'none', fontWeight: 600, fontSize: '0.82rem', px: 1.5, py: 0.8,
                        color: currentView === item.key ? 'primary.contrastText' : 'text.secondary',
                        bgcolor: currentView === item.key ? 'primary.main' : 'transparent',
                        '&:hover': { bgcolor: currentView === item.key ? 'primary.dark' : alpha(theme.palette.primary.main, 0.06) },
                        minWidth: 'auto',
                    }}
                >
                    {item.label}
                </Button>
            ))}
            <Button onClick={logout} startIcon={<LogoutIcon sx={{ fontSize: '1rem' }} />} size="small"
                sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600, fontSize: '0.82rem', px: 1.5, py: 0.8, color: 'error.main', ml: 0.5, '&:hover': { bgcolor: alpha(theme.palette.error.main, 0.06) } }}>
                Logout
            </Button>
        </Box>
    );

    // ── Mobile drawer ─────────────────────────────────────────────────────────
    const MobileDrawer = () => {
        const groups = [...new Set(navItems.map(n => n.group))];
        return (
            <Drawer
                open={drawerOpen}
                onClose={() => setDrawerOpen(false)}
                PaperProps={{
                    sx: {
                        width: DRAWER_WIDTH,
                        background: 'linear-gradient(180deg, #1a1f3a 0%, #0f1429 100%)',
                        color: 'white',
                        borderRight: 'none',
                    }
                }}
            >
                {/* Drawer Header */}
                <Box sx={{ px: 2.5, py: 2.5, display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                    <Box>
                        <Typography variant="h6" sx={{ fontWeight: 800, color: 'white', lineHeight: 1.2 }}>
                            {brandName}
                        </Typography>
                        <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)' }}>
                            {user?.username} · {user?.role}
                        </Typography>
                    </Box>
                    <IconButton onClick={() => setDrawerOpen(false)} sx={{ color: 'rgba(255,255,255,0.6)', '&:hover': { color: 'white', bgcolor: 'rgba(255,255,255,0.08)' } }}>
                        <CloseIcon />
                    </IconButton>
                </Box>

                {/* Nav Groups */}
                <Box sx={{ flex: 1, overflowY: 'auto', py: 1 }}>
                    {groups.map((group, gi) => (
                        <Box key={group}>
                            {gi > 0 && <Divider sx={{ borderColor: 'rgba(255,255,255,0.06)', mx: 2, my: 0.5 }} />}
                            <Typography variant="overline" sx={{ px: 2.5, pt: 1.5, pb: 0.5, display: 'block', color: 'rgba(255,255,255,0.35)', fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em' }}>
                                {GROUP_LABELS[group]}
                            </Typography>
                            <List dense disablePadding>
                                {navItems.filter(n => n.group === group).map(item => {
                                    const active = currentView === item.key;
                                    return (
                                        <ListItem key={item.key} disablePadding sx={{ px: 1.5, mb: 0.25 }}>
                                            <ListItemButton
                                                onClick={() => navigate(item.key)}
                                                sx={{
                                                    borderRadius: '12px', py: 1.2, px: 1.5,
                                                    bgcolor: active ? alpha(theme.palette.primary.main, 0.85) : 'transparent',
                                                    '&:hover': { bgcolor: active ? alpha(theme.palette.primary.main, 0.9) : 'rgba(255,255,255,0.06)' },
                                                    transition: 'all 0.2s ease',
                                                }}
                                            >
                                                <ListItemIcon sx={{ minWidth: 36, color: active ? 'white' : 'rgba(255,255,255,0.5)' }}>
                                                    {React.cloneElement(item.icon, { fontSize: 'small' })}
                                                </ListItemIcon>
                                                <ListItemText
                                                    primary={item.label}
                                                    primaryTypographyProps={{ fontSize: '0.88rem', fontWeight: active ? 700 : 500, color: active ? 'white' : 'rgba(255,255,255,0.75)' }}
                                                />
                                                {active && <ChevronRightIcon sx={{ fontSize: 18, color: 'rgba(255,255,255,0.7)' }} />}
                                            </ListItemButton>
                                        </ListItem>
                                    );
                                })}
                            </List>
                        </Box>
                    ))}
                </Box>

                {/* Drawer Footer — Logout */}
                <Box sx={{ px: 2, py: 2, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
                    <ListItemButton onClick={logout} sx={{ borderRadius: '12px', py: 1.2, px: 1.5, '&:hover': { bgcolor: alpha(theme.palette.error.main, 0.15) } }}>
                        <ListItemIcon sx={{ minWidth: 36, color: theme.palette.error.light }}>
                            <LogoutIcon fontSize="small" />
                        </ListItemIcon>
                        <ListItemText primary="Logout" primaryTypographyProps={{ fontSize: '0.88rem', fontWeight: 600, color: theme.palette.error.light }} />
                    </ListItemButton>
                </Box>
            </Drawer>
        );
    };

    // ── renderView ────────────────────────────────────────────────────────────
    const renderView = () => {
        switch (currentView) {
            case 'dashboard': return <DashboardView />;
            case 'resellers': return <ResellerManagementView />;
            case 'suppliers': return <SuppliersView />;
            case 'employees': return <EmployeesView />;
            case 'upstream-providers': return <UpstreamProviderManagementView customers={customers} />;
            case 'mikrotik-servers': return <MikrotikServerManagementView />;
            case 'subscriptions': return <SubscriptionsView customers={customers} pagination={pagination} subscriptionPlans={subscriptionPlans} businessSettings={businessSettings} refetchCustomers={refetchCustomers} setSnackbar={setSnackbar} currentPage={currentPage} setCurrentPage={setCurrentPage} itemsPerPage={itemsPerPage} setItemsPerPage={setItemsPerPage} searchQuery={searchQuery} setSearchQuery={setSearchQuery} customerSortBy={customerSortBy} setCustomerSortBy={setCustomerSortBy} customerResellerId={customerResellerId} setCustomerResellerId={setCustomerResellerId} />;
            case 'payments': return <PaymentsView />;
            case 'receipts': return <ReceiptsView />;
            case 'expenses': return <ExpensesView />;
            case 'reports': return <ReportsView />;
            case 'service': return <ServiceManagementView />;
            case 'enhanced-reports': return <EnhancedReportsView />;
            case 'subscription-plans': return <SubscriptionPlansView subscriptionPlans={subscriptionPlans} refetchSubscriptionPlans={refetchSubscriptionPlans} setSnackbar={setSnackbar} />;
            case 'messaging': return hasRole('admin') ? <MessagingView /> : <Typography>Access Denied</Typography>;
            case 'settings': return hasRole('admin') ? <SettingsView businessSettings={businessSettings} setBusinessSettings={setBusinessSettings} setSnackbar={setSnackbar} /> : <Typography>Access Denied</Typography>;
            case 'billing': return hasRole('admin') ? <BillingView /> : <Typography>Access Denied</Typography>;
            default:
                if (hasRole('employee') || hasRole('technician')) return <ServiceManagementView />;
                return <SubscriptionsView customers={customers} pagination={pagination} subscriptionPlans={subscriptionPlans} businessSettings={businessSettings} refetchCustomers={refetchCustomers} setSnackbar={setSnackbar} currentPage={currentPage} setCurrentPage={setCurrentPage} itemsPerPage={itemsPerPage} setItemsPerPage={setItemsPerPage} searchQuery={searchQuery} setSearchQuery={setSearchQuery} customerSortBy={customerSortBy} setCustomerSortBy={setCustomerSortBy} customerResellerId={customerResellerId} setCustomerResellerId={setCustomerResellerId} />;
        }
    };

    return (
        <Box sx={{ minHeight: '100vh', bgcolor: 'background.default', overflowX: 'hidden' }}>
            {/* ── AppBar ── */}
            <AppBar position="sticky" elevation={0} sx={{ bgcolor: 'white', borderBottom: `1px solid ${alpha(theme.palette.divider, 0.1)}`, color: 'text.primary', zIndex: theme.zIndex.drawer + 1 }}>
                <Toolbar sx={{ minHeight: { xs: 56, sm: 64 } }}>
                    {/* Hamburger — mobile only */}
                    {isMobile && (
                        <IconButton edge="start" onClick={() => setDrawerOpen(true)} sx={{ mr: 1.5, color: 'text.primary', bgcolor: alpha(theme.palette.primary.main, 0.06), borderRadius: '10px', '&:hover': { bgcolor: alpha(theme.palette.primary.main, 0.12) } }}>
                            <MenuIcon />
                        </IconButton>
                    )}

                    {/* Per-tenant logo + name (falls back to the servicesBills brand) */}
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexGrow: 1, minWidth: 0 }}>
                        {logoSrc && (
                            <Box component="img" src={logoSrc} alt=""
                                 sx={{ height: 32, width: 32, borderRadius: '8px', objectFit: 'cover' }} />
                        )}
                        <Typography variant="h6" noWrap sx={{ fontWeight: 800, color: 'primary.main', fontSize: { xs: '1rem', sm: '1.15rem' } }}>
                            {brandName}
                        </Typography>
                    </Box>

                    {/* Current view badge — mobile */}
                    {isMobile && (
                        <Chip label={currentLabel} size="small" sx={{ bgcolor: alpha(theme.palette.primary.main, 0.1), color: 'primary.main', fontWeight: 700, fontSize: '0.75rem', mr: 1 }} />
                    )}

                    {/* User info — desktop */}
                    {user && !isMobile && (
                        <Typography variant="body2" sx={{ color: 'text.secondary', fontWeight: 500 }}>
                            {user.username} · <strong>{user.role}</strong>
                        </Typography>
                    )}
                </Toolbar>
            </AppBar>

            {/* ── Desktop Nav ── */}
            {!isMobile && <DesktopNav />}

            {/* ── Mobile Drawer ── */}
            {isMobile && <MobileDrawer />}

            {/* ── Page Content ── */}
            <Box sx={{ px: { xs: 1, sm: 2, md: 3 }, py: { xs: 1.5, sm: 2, md: 3 } }}>
                {renderView()}
            </Box>

            {/* ── Back to Top (mobile) ── */}
            {isMobile && (
                <Zoom in={scrollTrigger}>
                    <Fab
                        size="small"
                        color="primary"
                        aria-label="Scroll back to top"
                        onClick={scrollToTop}
                        sx={{ position: 'fixed', bottom: 24, right: 16, zIndex: theme.zIndex.tooltip }}
                    >
                        <KeyboardArrowUpIcon />
                    </Fab>
                </Zoom>
            )}
        </Box>
    );
};



// This component now decides whether to show the Login/Register screens or the MainApp
const AppContent = () => {
    const { isAuthenticated, setSnackbar, logout, user } = useAppContext();
    const location = useLocation();
    const isSuperadmin = (user?.role || '') === 'superadmin';

    const [customers, setCustomers] = useState([]);
    const [pagination, setPagination] = useState({ pages: 1, total: 0, current_page: 1 });
    const [subscriptionPlans, setSubscriptionPlans] = useState([]);
    const [businessSettings, setBusinessSettings] = useState(null);
    const [loading, setLoading] = useState(true); // --- FIX: Start with loading true

    // --- PAGINATION STATE (lifted from SubscriptionsView) ---
    const [currentPage, setCurrentPage] = useState(1);
    const [itemsPerPage, setItemsPerPage] = useState(25);
    const [searchQuery, setSearchQuery] = useState('');
    const [customerSortBy, setCustomerSortBy] = useState('expiry_date');
    const [customerResellerId, setCustomerResellerId] = useState('');

    useEffect(() => {
        if (businessSettings) {
            if (businessSettings.business_name) {
                document.title = businessSettings.business_name;
            }
            const url = resolveLogoUrl(businessSettings.logo_url) || DEFAULT_LOGO_PATH;

            let link = document.querySelector("link[rel~='icon']");
            if (!link) {
                link = document.createElement('link');
                link.rel = 'icon';
                document.head.appendChild(link);
            }
            link.href = url;

            let appleLink = document.querySelector("link[rel='apple-touch-icon']");
            if (!appleLink) {
                appleLink = document.createElement('link');
                appleLink.rel = 'apple-touch-icon';
                document.head.appendChild(appleLink);
            }
            appleLink.href = url;
        }
    }, [businessSettings]);

    const refetchSubscriptionPlans = useCallback(async () => {
        try {
            const plansRes = await apiService.fetchSubscriptionPlans();
            setSubscriptionPlans(plansRes || []);
        } catch (error) {
            console.error("Error fetching subscription plans:", error);
            setSnackbar({ open: true, message: 'Failed to refresh subscription plans.', severity: 'error' });
        }
    }, [setSnackbar]);

    const refetchCustomers = useCallback(async (page = 1, per_page = 25, searchQuery = '', sortBy = 'expiry_date', resellerId = '') => {
        try {
            // The apiService.fetchCustomers already returns the data object.
            const response = await apiService.fetchCustomers(page, per_page, searchQuery, sortBy, resellerId);
            setCustomers(response.customers || []);
            setPagination({
                total: response.total,
                pages: response.pages,
                currentPage: response.current_page
            });
        } catch (error) {
            console.error("Error fetching customers:", error);
            setSnackbar({ open: true, message: 'Failed to load customers.', severity: 'error' });
        }
    }, [setSnackbar]);

    // Debounced search query
    const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('');

    useEffect(() => {
        const timerId = setTimeout(() => {
            setDebouncedSearchQuery(searchQuery);
        }, 500);
        return () => clearTimeout(timerId);
    }, [searchQuery]);

    // Auto-fetch when pagination state changes
    useEffect(() => {
        if (isAuthenticated && !isSuperadmin) {
            refetchCustomers(currentPage, itemsPerPage, debouncedSearchQuery, customerSortBy, customerResellerId);
        }
    }, [currentPage, itemsPerPage, debouncedSearchQuery, customerSortBy, customerResellerId, isAuthenticated, isSuperadmin, refetchCustomers]);

    useEffect(() => {
        if (isAuthenticated && !isSuperadmin) {
            const loadInitialData = async () => {
                setLoading(true);
                try {
                    // --- FIX: Correctly await and destructure responses ---
                    const [customersRes, plansRes, settingsRes] = await Promise.all([
                        apiService.fetchCustomers(),
                        apiService.fetchSubscriptionPlans(),
                        apiService.fetchBusinessSettings(),
                    ]);

                    // --- FIX: Remove the extra .data access ---
                    setCustomers(customersRes.customers || []);
                    setPagination({
                        total: customersRes.total,
                        pages: customersRes.pages,
                        currentPage: customersRes.current_page
                    });
                    setSubscriptionPlans(plansRes || []);
                    if (settingsRes.data?.settings) {
                        setBusinessSettings(settingsRes.data.settings);
                    }
                } catch (error) {
                    console.error("Error loading initial data:", error);
                    setSnackbar({ open: true, message: 'Failed to load initial data. Please check the console for details.', severity: 'error' });
                } finally {
                    setLoading(false);
                }
            };
            loadInitialData();
        } else {
            setLoading(false); // If not authenticated, stop loading
        }
    }, [isAuthenticated, setSnackbar]);


    // Public deep-link screens render regardless of auth (email links land here).
    if (location.pathname === '/verify') return <VerifyEmailView />;
    if (location.pathname === '/reset-password') return <ResetPasswordView />;
    if (location.pathname === '/forgot-password') return <ForgotPasswordView />;

    if (!isAuthenticated) {
        if (location.pathname === '/register') return <RegisterView />;
        if (location.pathname === '/login') return <LoginView />;
        return <LandingView />; // public landing at '/' (and any other path)
    }

    // Platform operators get the cross-tenant admin console, not the tenant app.
    if (isSuperadmin) {
        return <SuperAdminView />;
    }

    // --- FIX: Show loader while loading is true, even after authentication ---
    if (loading) {
        return (
            <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
                <CircularProgress />
                <Typography sx={{ ml: 2 }}>Loading initial data...</Typography>
            </Box>
        );
    }

    return <MainApp
        customers={customers}
        pagination={pagination}
        subscriptionPlans={subscriptionPlans}
        businessSettings={businessSettings}
        refetchCustomers={refetchCustomers}
        refetchSubscriptionPlans={refetchSubscriptionPlans}
        setSnackbar={setSnackbar}
        setBusinessSettings={setBusinessSettings}
        currentPage={currentPage}
        setCurrentPage={setCurrentPage}
        itemsPerPage={itemsPerPage}
        setItemsPerPage={setItemsPerPage}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        customerSortBy={customerSortBy}
        setCustomerSortBy={setCustomerSortBy}
        customerResellerId={customerResellerId}
        setCustomerResellerId={setCustomerResellerId}
    />;
};


// The final App component wraps everything in the context provider
function App() {
    return (
        <ThemeProvider theme={theme}>
            <CssBaseline />
            <BrowserRouter>
                <AppContextProvider>
                    <AppContentWrapper />
                </AppContextProvider>
            </BrowserRouter>
        </ThemeProvider>
    );
}

// A new wrapper component to handle the snackbar logic
const AppContentWrapper = () => {
    const { snackbar, setSnackbar } = useAppContext();

    const handleCloseSnackbar = (event, reason) => {
        if (reason === 'clickaway') {
            return;
        }
        setSnackbar({ ...snackbar, open: false });
    };

    return (
        <>
            <AppContent />
            <Snackbar open={snackbar.open} autoHideDuration={6000} onClose={handleCloseSnackbar}>
                <Alert onClose={handleCloseSnackbar} severity={snackbar.severity} sx={{ width: '100%' }}>
                    {snackbar.message}
                </Alert>
            </Snackbar>
        </>
    );
};

export default App;
