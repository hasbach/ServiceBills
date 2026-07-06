import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, TextField, CircularProgress,
    Avatar, Grid, Divider, Switch, alpha, useTheme,
    Alert, Collapse, InputAdornment, IconButton,
    ToggleButton, ToggleButtonGroup, Tab, Tabs
} from '@mui/material';
import {
    Business as BusinessIcon,
    WhatsApp as WhatsAppIcon,
    Save as SaveIcon,
    Visibility as VisibilityIcon,
    VisibilityOff as VisibilityOffIcon,
    Link as LinkIcon,
    Api as ApiIcon,
    Info as InfoIcon,
    Message as MessageIcon,
    People as PeopleIcon,
    LocationOn as LocationOnIcon,
    SystemUpdateAlt as UpdateIcon,
    CloudDownload as CloudDownloadIcon,
    CheckCircle as CheckCircleIcon,
    Storage as StorageIcon,
    Refresh as RefreshIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import ExpenseCategoryManager from './ExpenseCategoryManager.js';
import UserManagement from './UserManagement.js';
import SectorManager from './SectorManager.js';

const API_BASE_URL = process.env.NODE_ENV === 'production' ? '' : 'http://localhost:5000';

// Default WhatsApp settings (outside component to avoid stale closures)
const DEFAULT_WA = {
    enabled: false, mode: 'deeplink',
    phone_number_id: '', business_account_id: '', app_id: '',
    app_secret: '', access_token: '', api_version: 'v19.0',
    template_payment_paid: 'payment_confirmation',
    template_subscription_renewed: 'subscription_renewal',
    template_payment_reminder: 'payment_reminder',
    template_bulk_outage: 'outage_alert',
    template_bulk_maintenance: 'maintenance_alert',
    template_bulk_feature: 'feature_update',
    template_bulk_offer: 'special_offer',
    template_language: 'en',
    forwarding_mobile: '',
    webhook_verify_token: 'delta_net_whatsapp_secret',
    // eslint-disable-next-line no-template-curly-in-string
    deeplink_msg_payment: 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!',
    // eslint-disable-next-line no-template-curly-in-string
    deeplink_msg_renewal: 'Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!',
};

// ── Section wrapper ──────────────────────────────────────────────────────────
const Section = ({ icon, title, subtitle, color, children }) => {
    const theme = useTheme();
    return (
        <Paper elevation={0} sx={{
            p: 3, mb: 3, borderRadius: '20px',
            background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
            border: `1px solid ${alpha(color || theme.palette.primary.main, 0.12)}`
        }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3 }}>
                <Box sx={{ width: 44, height: 44, borderRadius: '14px', bgcolor: alpha(color || theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {React.cloneElement(icon, { sx: { color: color || theme.palette.primary.main, fontSize: 22 } })}
                </Box>
                <Box>
                    <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>{title}</Typography>
                    {subtitle && <Typography variant="caption" color="text.secondary">{subtitle}</Typography>}
                </Box>
            </Box>
            {children}
        </Paper>
    );
};

// ── Main Component ────────────────────────────────────────────────────────────
const SettingsView = ({ businessSettings, setBusinessSettings, setSnackbar }) => {
    const { apiService } = useAppContext();
    const theme = useTheme();
    const [tab, setTab] = useState(0);

    // ── Business form state ───────────────────────────────────────────────────
    const [bizForm, setBizForm] = useState({
        business_name: '', address: '', mobile: '', email: '', website: ''
    });
    const [logoFile, setLogoFile] = useState(null);
    const [logoPreview, setLogoPreview] = useState(null);
    const [bizLoading, setBizLoading] = useState(false);

    useEffect(() => {
        if (businessSettings) {
            setBizForm({
                business_name: businessSettings.business_name || '',
                address: businessSettings.address || '',
                mobile: businessSettings.mobile || '',
                email: businessSettings.email || '',
                website: businessSettings.website || ''
            });
            if (businessSettings.logo_url) {
                setLogoPreview(`${API_BASE_URL}${businessSettings.logo_url}`);
            }
        }
    }, [businessSettings]);

    const handleBizSubmit = async (e) => {
        e.preventDefault();
        setBizLoading(true);
        const fd = new FormData();
        Object.keys(bizForm).forEach(k => fd.append(k, bizForm[k]));
        if (logoFile) fd.append('logo', logoFile);
        try {
            const response = await apiService.saveBusinessSettings(fd);
            setBusinessSettings(response.data.settings);
            setSnackbar({ open: true, message: 'Business settings saved!', severity: 'success' });
        } catch {
            setSnackbar({ open: true, message: 'Failed to save settings.', severity: 'error' });
        } finally {
            setBizLoading(false);
        }
    };

    // ── WhatsApp state ────────────────────────────────────────────────────────
    const [waForm, setWaForm] = useState(DEFAULT_WA);
    const [waLoading, setWaLoading] = useState(false);
    const [waFetching, setWaFetching] = useState(true);
    const [showSecret, setShowSecret] = useState(false);
    const [showToken, setShowToken] = useState(false);

    const fetchWASettings = useCallback(async () => {
        setWaFetching(true);
        try {
            const res = await apiService.fetchWhatsAppSettings();
            if (res.data?.settings) setWaForm(prev => ({ ...DEFAULT_WA, ...res.data.settings }));
        } catch (e) {
            console.error('Failed to load WhatsApp settings', e);
        } finally {
            setWaFetching(false);
        }
    }, [apiService]);

    useEffect(() => { fetchWASettings(); }, [fetchWASettings]);

    const handleWASave = async () => {
        setWaLoading(true);
        try {
            await apiService.saveWhatsAppSettings(waForm);
            setSnackbar({ open: true, message: 'WhatsApp settings saved!', severity: 'success' });
        } catch (err) {
            const detail = err?.response?.data?.error || err?.message || 'Unknown error';
            console.error('WhatsApp save error:', err?.response || err);
            setSnackbar({ open: true, message: `Failed to save: ${detail}`, severity: 'error' });
        } finally {
            setWaLoading(false);
        }
    };

    const [linkingWaba, setLinkingWaba] = useState(false);
    const handleLinkWaba = async () => {
        setLinkingWaba(true);
        try {
            await apiService.saveWhatsAppSettings(waForm);
            const res = await apiService.subscribeWaba();
            setSnackbar({ open: true, message: res?.data?.message || 'Successfully linked Webhook to Meta Account!', severity: 'success' });
        } catch (err) {
            const detail = err?.response?.data?.error || err?.message || 'Unknown error';
            setSnackbar({ open: true, message: `Failed to link: ${detail}`, severity: 'error' });
        } finally {
            setLinkingWaba(false);
        }
    };

    const waField = (key) => ({ value: waForm[key], onChange: (e) => setWaForm(f => ({ ...f, [key]: e.target.value })) });

    // ── System Update state ───────────────────────────────────────────────────
    const [sysUpdate, setSysUpdate] = useState({
        current_version: '1.4.0', latest_available_version: '1.4.0',
        github_repo: 'hasbach/servicesBills', auto_update_enabled: false,
        auto_update_time: '03:00', platform: 'pythonanywhere',
        last_checked_at: null, last_updated_at: null, release_notes: ''
    });
    const [sysLoading, setSysLoading] = useState(false);
    const [checkingUpdate, setCheckingUpdate] = useState(false);
    const [applyingUpdate, setApplyingUpdate] = useState(false);
    const [updateLogs, setUpdateLogs] = useState([]);

    const fetchSysUpdate = useCallback(async () => {
        try {
            const res = await apiService.fetchSystemUpdateStatus();
            if (res?.data?.status) setSysUpdate(res.data.status);
        } catch (e) {
            console.error('Failed to fetch system update status', e);
        }
    }, [apiService]);

    useEffect(() => { fetchSysUpdate(); }, [fetchSysUpdate]);

    const handleSaveSysSettings = async (e) => {
        if (e) e.preventDefault();
        setSysLoading(true);
        try {
            const res = await apiService.saveSystemUpdateSettings(sysUpdate);
            if (res?.data?.status) setSysUpdate(res.data.status);
            setSnackbar({ open: true, message: 'System update settings saved!', severity: 'success' });
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to save update settings.', severity: 'error' });
        } finally {
            setSysLoading(false);
        }
    };

    const handleCheckUpdate = async () => {
        setCheckingUpdate(true);
        try {
            const res = await apiService.checkForSystemUpdates();
            if (res?.data?.status) setSysUpdate(res.data.status);
            setSnackbar({ open: true, message: res?.data?.message || 'Checked GitHub repo!', severity: 'info' });
        } catch (err) {
            setSnackbar({ open: true, message: 'Failed to check GitHub repo.', severity: 'error' });
        } finally {
            setCheckingUpdate(false);
        }
    };

    const handleApplyUpdate = async () => {
        if (!window.confirm("Are you sure you want to download and apply the latest update from GitHub now? Database migrations will run safely without data loss.")) return;
        setApplyingUpdate(true);
        setUpdateLogs([]);
        try {
            const res = await apiService.applySystemUpdate();
            if (res?.data?.status) setSysUpdate(res.data.status);
            if (res?.data?.logs) setUpdateLogs(res.data.logs);
            setSnackbar({ open: true, message: res?.data?.message || 'Update applied successfully!', severity: 'success' });
        } catch (err) {
            setSnackbar({ open: true, message: 'Error applying update.', severity: 'error' });
        } finally {
            setApplyingUpdate(false);
        }
    };

    // ── Render ────────────────────────────────────────────────────────────────
    return (
        <Box sx={{ p: { xs: 1.5, sm: 2, md: 3 }, background: 'linear-gradient(135deg, #f6f9fc 0%, #ffffff 100%)', minHeight: '100vh' }}>

            {/* Header */}
            <Paper elevation={0} sx={{ p: { xs: 2, sm: 3, md: 4 }, mb: 4, borderRadius: '24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', position: 'relative', overflow: 'hidden' }}>
                <Box sx={{ position: 'absolute', top: -50, right: -50, width: 200, height: 200, borderRadius: '50%', background: alpha('#fff', 0.1) }} />
                <Box sx={{ position: 'relative', zIndex: 1 }}>
                    <Typography variant="h4" sx={{ fontWeight: 700, mb: 1, fontSize: { xs: '1.3rem', sm: '1.75rem', md: '2.125rem' } }}>Settings</Typography>
                    <Typography variant="body1" sx={{ opacity: 0.9, fontSize: { xs: '0.85rem', sm: '1rem' } }}>Configure your business profile, messaging and integrations</Typography>
                </Box>
            </Paper>

            {/* Tabs */}
            <Paper elevation={0} sx={{ borderRadius: '16px', mb: 3, overflow: 'hidden', border: `1px solid ${alpha(theme.palette.divider, 0.1)}` }}>
                <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 2, '& .MuiTab-root': { textTransform: 'none', fontWeight: 600 } }}>
                    <Tab icon={<BusinessIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Business Details" />
                    <Tab icon={<WhatsAppIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="WhatsApp Notifications" />
                    <Tab icon={<MessageIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Expense Categories" />
                    <Tab icon={<PeopleIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="User Management" />
                    <Tab icon={<LocationOnIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Sectors" />
                    <Tab icon={<UpdateIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Software & System Updates" />
                </Tabs>
            </Paper>

            {/* ── Tab 0: Business Details ── */}
            {tab === 0 && (
                <Section icon={<BusinessIcon />} title="Business Details" subtitle="Your company information shown on receipts" color={theme.palette.primary.main}>
                    <form onSubmit={handleBizSubmit}>
                        <Grid container spacing={3}>
                            <Grid item xs={12} sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                                <Avatar src={logoPreview} sx={{ width: 100, height: 100, mb: 2, boxShadow: `0 4px 20px ${alpha(theme.palette.primary.main, 0.2)}` }} />
                                <Button variant="outlined" component="label" sx={{ borderRadius: '10px', textTransform: 'none' }}>
                                    Upload Logo
                                    <input type="file" hidden accept="image/*" onChange={e => { const f = e.target.files[0]; if (f) { setLogoFile(f); setLogoPreview(URL.createObjectURL(f)); } }} />
                                </Button>
                            </Grid>
                            <Grid item xs={12} md={6}>
                                <TextField fullWidth label="Business Name" value={bizForm.business_name} onChange={e => setBizForm(f => ({ ...f, business_name: e.target.value }))} sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                            </Grid>
                            <Grid item xs={12} md={6}>
                                <TextField fullWidth label="Mobile" value={bizForm.mobile} onChange={e => setBizForm(f => ({ ...f, mobile: e.target.value }))} sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                            </Grid>
                            <Grid item xs={12}>
                                <TextField fullWidth label="Address" value={bizForm.address} onChange={e => setBizForm(f => ({ ...f, address: e.target.value }))} sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                            </Grid>
                            <Grid item xs={12} md={6}>
                                <TextField fullWidth label="Email" type="email" value={bizForm.email} onChange={e => setBizForm(f => ({ ...f, email: e.target.value }))} sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                            </Grid>
                            <Grid item xs={12} md={6}>
                                <TextField fullWidth label="Website" value={bizForm.website} onChange={e => setBizForm(f => ({ ...f, website: e.target.value }))} sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                            </Grid>
                        </Grid>
                        <Button type="submit" variant="contained" startIcon={bizLoading ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />} disabled={bizLoading}
                            sx={{ mt: 3, borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 4, py: 1.5 }}>
                            {bizLoading ? 'Saving…' : 'Save Business Details'}
                        </Button>
                    </form>
                </Section>
            )}

            {/* ── Tab 1: WhatsApp ── */}
            {tab === 1 && (
                <Box>
                    {waFetching ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}><CircularProgress /></Box>
                    ) : (
                        <>
                            {/* Master Toggle */}
                            <Section icon={<WhatsAppIcon />} title="WhatsApp Notifications" subtitle="Send messages to customers on payment or renewal" color="#25D366">
                                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
                                    <Box>
                                        <Typography variant="body1" sx={{ fontWeight: 600 }}>Enable WhatsApp Notifications</Typography>
                                        <Typography variant="body2" color="text.secondary">Show WhatsApp action buttons on payment records</Typography>
                                    </Box>
                                    <Switch checked={waForm.enabled} onChange={e => setWaForm(f => ({ ...f, enabled: e.target.checked }))}
                                        sx={{ '& .MuiSwitch-switchBase.Mui-checked': { color: '#25D366' }, '& .MuiSwitch-switchBase.Mui-checked + .MuiSwitch-track': { bgcolor: '#25D366' } }} />
                                </Box>

                                <Collapse in={waForm.enabled}>
                                    <Divider sx={{ my: 3 }} />
                                    {/* Mode Toggle */}
                                    <Box sx={{ mb: 3 }}>
                                        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1.5 }}>Sending Mode</Typography>
                                        <ToggleButtonGroup value={waForm.mode} exclusive onChange={(_, v) => v && setWaForm(f => ({ ...f, mode: v }))} sx={{ gap: 1 }}>
                                            <ToggleButton value="deeplink" sx={{ borderRadius: '12px !important', textTransform: 'none', px: 2, py: 1, border: '1px solid !important', fontWeight: 600, '&.Mui-selected': { bgcolor: alpha('#25D366', 0.1), borderColor: '#25D366 !important', color: '#25D366' } }}>
                                                <LinkIcon sx={{ mr: 1, fontSize: 18 }} /> Deep Link (Manual)
                                            </ToggleButton>
                                            <ToggleButton value="api" sx={{ borderRadius: '12px !important', textTransform: 'none', px: 2, py: 1, border: '1px solid !important', fontWeight: 600, '&.Mui-selected': { bgcolor: alpha(theme.palette.primary.main, 0.1), borderColor: `${theme.palette.primary.main} !important`, color: theme.palette.primary.main } }}>
                                                <ApiIcon sx={{ mr: 1, fontSize: 18 }} /> Meta Cloud API (Auto)
                                            </ToggleButton>
                                        </ToggleButtonGroup>

                                        <Alert severity="info" icon={<InfoIcon />} sx={{ mt: 2, borderRadius: '12px' }}>
                                            {waForm.mode === 'deeplink'
                                                ? '📱 A "Send via WhatsApp" button will appear on payment cards. Clicking it opens WhatsApp with a pre-filled message — the user taps Send.'
                                                : '🤖 Messages are sent automatically via the Meta Cloud API when a payment is marked as paid or a subscription is renewed. Requires approved message templates.'}
                                        </Alert>
                                    </Box>
                                </Collapse>
                            </Section>

                            {/* Deep Link Message Templates */}
                            <Collapse in={waForm.enabled && waForm.mode === 'deeplink'}>
                                <Section icon={<MessageIcon />} title="Message Templates — Deep Link" subtitle="Use {customer_name}, {amount}, {expiry_date} as placeholders" color="#25D366">
                                    <Grid container spacing={2}>
                                        <Grid item xs={12}>
                                            <TextField fullWidth multiline rows={3} label="Payment Received Message"
                                                {...waField('deeplink_msg_payment')}
                                                helperText="Placeholders: {customer_name}, {amount}"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12}>
                                            <TextField fullWidth multiline rows={3} label="Subscription Renewed Message"
                                                {...waField('deeplink_msg_renewal')}
                                                helperText="Placeholders: {customer_name}, {expiry_date}"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                    </Grid>
                                    <Alert severity="success" sx={{ mt: 2, borderRadius: '12px' }}>
                                        <strong>Preview:</strong> {waForm.deeplink_msg_payment.replace('{customer_name}', 'John Doe').replace('{amount}', '50.00')}
                                    </Alert>
                                </Section>
                            </Collapse>

                            {/* Meta API Settings */}
                            <Collapse in={waForm.enabled && waForm.mode === 'api'}>
                                <Section icon={<ApiIcon />} title="Meta Cloud API Credentials"
                                    subtitle="Get these from your Meta for Developers dashboard (developers.facebook.com)" color={theme.palette.primary.main}>
                                    <Alert severity="warning" sx={{ mb: 3, borderRadius: '12px' }}>
                                        ⚠️ This section stores your API credentials. Keep these secret — never share them. You must have a verified Meta Business account and approved message templates.
                                    </Alert>
                                    <Grid container spacing={2}>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="App ID" placeholder="e.g. 123456789012345" {...waField('app_id')}
                                                helperText="From your Meta App Dashboard → App ID"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="App Secret" type={showSecret ? 'text' : 'password'} placeholder="Your app secret"
                                                {...waField('app_secret')} helperText="Settings → Basic → App Secret"
                                                InputProps={{ endAdornment: <InputAdornment position="end"><IconButton size="small" onClick={() => setShowSecret(s => !s)}>{showSecret ? <VisibilityOffIcon /> : <VisibilityIcon />}</IconButton></InputAdornment> }}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Phone Number ID" placeholder="e.g. 103843228738291" {...waField('phone_number_id')}
                                                helperText="WhatsApp → API Setup → Phone Number ID"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Business Account ID (WABA ID)" placeholder="e.g. 102290129000001" {...waField('business_account_id')}
                                                helperText="WhatsApp → API Setup → WhatsApp Business Account ID"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12}>
                                            <TextField fullWidth label="Permanent Access Token" type={showToken ? 'text' : 'password'} placeholder="EAA..." multiline={showToken} rows={showToken ? 3 : 1}
                                                {...waField('access_token')} helperText="System User token from Business Settings → System Users → Generate Token"
                                                InputProps={{ endAdornment: <InputAdornment position="end"><IconButton size="small" onClick={() => setShowToken(s => !s)}>{showToken ? <VisibilityOffIcon /> : <VisibilityIcon />}</IconButton></InputAdornment> }}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={4}>
                                            <TextField fullWidth label="API Version" placeholder="v19.0" {...waField('api_version')}
                                                helperText="Latest stable: v19.0"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={4}>
                                            <TextField fullWidth label="Template Language Code" placeholder="en" {...waField('template_language')}
                                                helperText="e.g. en, ar, fr, es"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                    </Grid>

                                    <Divider sx={{ my: 3 }} />
                                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 2 }}>Approved Template Names</Typography>
                                    <Alert severity="info" sx={{ mb: 2, borderRadius: '12px' }}>
                                        These must exactly match the template names you approved in Meta Business Manager → WhatsApp → Message Templates.
                                    </Alert>
                                    <Grid container spacing={2}>
                                        <Grid item xs={12} md={4}>
                                            <TextField fullWidth label="Payment Received Template" placeholder="payment_confirmation" {...waField('template_payment_paid')}
                                                helperText="Triggered when a payment is marked as paid"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={4}>
                                            <TextField fullWidth label="Subscription Renewed Template" placeholder="subscription_renewal" {...waField('template_subscription_renewed')}
                                                helperText="Triggered when subscription is renewed"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={4}>
                                            <TextField fullWidth label="Payment Reminder Template" placeholder="payment_reminder" {...waField('template_payment_reminder')}
                                                helperText="For future manual or scheduled reminders"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Outage Template" placeholder="outage_alert" {...waField('template_bulk_outage')}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Maintenance Template" placeholder="maintenance_alert" {...waField('template_bulk_maintenance')}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Feature Template" placeholder="feature_update" {...waField('template_bulk_feature')}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Offer Template" placeholder="special_offer" {...waField('template_bulk_offer')}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                    </Grid>

                                    <Divider sx={{ my: 3 }} />
                                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1, color: theme.palette.primary.main }}>
                                        📩 Webhook & Incoming Reply Forwarding
                                    </Typography>
                                    <Alert severity="info" sx={{ mb: 2, borderRadius: '12px' }}>
                                        When customers reply to your messages, Meta sends their replies to your webhook. Configure your business mobile number below to receive instant forwarded alerts on your phone!
                                    </Alert>
                                    <Grid container spacing={2}>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Forwarding Mobile Number (Business/Personal)" placeholder="e.g. 201012345678 or 010..." {...waField('forwarding_mobile')}
                                                helperText="Incoming customer replies will be forwarded to this WhatsApp number instantly"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Webhook Verify Token" placeholder="delta_net_whatsapp_secret" {...waField('webhook_verify_token')}
                                                helperText="Use this exact secret token in Meta Developer Console when configuring your Webhook"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12}>
                                            <Alert severity="success" sx={{ borderRadius: '12px', bgcolor: '#f0fdf4', color: '#166534', border: '1px solid #bbf7d0', mb: 2 }}>
                                                <strong>Your Webhook URL for Meta Console:</strong> <code>{window.location.origin}/api/whatsapp/webhook</code>
                                            </Alert>
                                            <Button variant="outlined" color="primary" onClick={handleLinkWaba} disabled={linkingWaba}
                                                sx={{ borderRadius: '12px', fontWeight: 600, textTransform: 'none' }}>
                                                {linkingWaba ? 'Linking to Meta Account...' : '🔗 Force Link Webhook to Meta Account'}
                                            </Button>
                                        </Grid>
                                    </Grid>

                                    <Box sx={{ mt: 3, p: 2, borderRadius: '12px', bgcolor: alpha(theme.palette.primary.main, 0.05), border: `1px solid ${alpha(theme.palette.primary.main, 0.15)}` }}>
                                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontWeight: 600, mb: 0.5 }}>📚 Quick Setup Reference</Typography>
                                        <Typography variant="caption" color="text.secondary">
                                            1. Go to <strong>developers.facebook.com</strong> → Create App → Business type<br />
                                            2. Add <strong>WhatsApp</strong> product → Get Phone Number ID + WABA ID<br />
                                            3. Create a <strong>System User</strong> in Business Settings → Generate token with whatsapp_business_messaging permission<br />
                                            4. Submit message templates for approval in <strong>Meta Business Manager</strong><br />
                                            5. Paste credentials above and save
                                        </Typography>
                                    </Box>
                                </Section>
                            </Collapse>

                            {/* Save WhatsApp */}
                            <Button variant="contained" startIcon={waLoading ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
                                onClick={handleWASave} disabled={waLoading}
                                sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 4, py: 1.5, bgcolor: '#25D366', '&:hover': { bgcolor: '#128C7E' } }}>
                                {waLoading ? 'Saving…' : 'Save WhatsApp Settings'}
                            </Button>
                        </>
                    )}
                </Box>
            )}

            {/* ── Tab 2: Expense Categories ── */}
            {tab === 2 && (
                <Section icon={<MessageIcon />} title="Expense Categories" subtitle="Manage the categories used to classify expenses" color={theme.palette.warning.main}>
                    <ExpenseCategoryManager />
                </Section>
            )}

            {/* ── Tab 3: User Management ── */}
            {tab === 3 && (
                <UserManagement />
            )}

            {/* Tab 4: Sectors */}
            {tab === 4 && (
                <SectorManager />
            )}

            {/* ── Tab 5: Software & System Updates ── */}
            {tab === 5 && (
                <Box>
                    {/* Status Card */}
                    <Paper elevation={0} sx={{
                        p: 3, mb: 3, borderRadius: '20px',
                        background: sysUpdate.latest_available_version > sysUpdate.current_version 
                            ? 'linear-gradient(135deg, #1e293b 0%, #0f172a 100%)'
                            : 'linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%)',
                        color: 'white', position: 'relative', overflow: 'hidden'
                    }}>
                        <Grid container spacing={3} alignItems="center">
                            <Grid item xs={12} md={7}>
                                <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1 }}>
                                    <Avatar sx={{ bgcolor: alpha('#10b981', 0.2), color: '#10b981', width: 50, height: 50 }}>
                                        <CloudDownloadIcon sx={{ fontSize: 28 }} />
                                    </Avatar>
                                    <Box>
                                        <Typography variant="h5" sx={{ fontWeight: 700 }}>
                                            {sysUpdate.latest_available_version > sysUpdate.current_version 
                                                ? `🚀 Update Available: v${sysUpdate.latest_available_version}`
                                                : `✅ System is up to date (v${sysUpdate.current_version})`}
                                        </Typography>
                                        <Typography variant="body2" sx={{ opacity: 0.8 }}>
                                            Repository: {sysUpdate.github_repo} | Platform: {sysUpdate.platform.toUpperCase()}
                                        </Typography>
                                    </Box>
                                </Box>
                                <Typography variant="body2" sx={{ mt: 2, p: 1.5, bgcolor: alpha('#fff', 0.05), borderRadius: '10px', fontFamily: 'monospace' }}>
                                    Last checked: {sysUpdate.last_checked_at || 'Never'} | Last updated: {sysUpdate.last_updated_at || 'Initial release'}
                                </Typography>
                            </Grid>
                            <Grid item xs={12} md={5} sx={{ display: 'flex', gap: 2, justifyContent: { xs: 'flex-start', md: 'flex-end' }, flexWrap: 'wrap' }}>
                                <Button
                                    variant="outlined"
                                    startIcon={checkingUpdate ? <CircularProgress size={16} color="inherit" /> : <RefreshIcon />}
                                    onClick={handleCheckUpdate}
                                    disabled={checkingUpdate}
                                    sx={{ color: 'white', borderColor: alpha('#fff', 0.3), borderRadius: '12px', textTransform: 'none', fontWeight: 600 }}
                                >
                                    Check GitHub
                                </Button>
                                <Button
                                    variant="contained"
                                    color="success"
                                    startIcon={applyingUpdate ? <CircularProgress size={16} color="inherit" /> : <UpdateIcon />}
                                    onClick={handleApplyUpdate}
                                    disabled={applyingUpdate}
                                    sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 700, px: 3, boxShadow: '0 4px 14px rgba(16, 185, 129, 0.4)' }}
                                >
                                    {applyingUpdate ? 'Upgrading & Migrating...' : 'Install Update Now'}
                                </Button>
                            </Grid>
                        </Grid>
                    </Paper>

                    {/* Console Output Logs if any */}
                    {updateLogs.length > 0 && (
                        <Paper elevation={0} sx={{ p: 2.5, mb: 3, borderRadius: '16px', bgcolor: '#0f172a', color: '#38bdf8', fontFamily: 'monospace', fontSize: '0.85rem' }}>
                            <Typography variant="subtitle2" sx={{ color: '#94a3b8', mb: 1, fontWeight: 700 }}>
                                ⚡ Deployment & Database Migration Log:
                            </Typography>
                            {updateLogs.map((log, idx) => (
                                <Box key={idx} sx={{ py: 0.5, borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                    &gt; {log}
                                </Box>
                            ))}
                        </Paper>
                    )}

                    {/* Settings Form */}
                    <Section icon={<StorageIcon />} title="Deployment & Automatic Update Settings" subtitle="Configure automated overnight syncing with GitHub and safe database schema upgrades">
                        <form onSubmit={handleSaveSysSettings}>
                            <Grid container spacing={3}>
                                <Grid item xs={12} md={6}>
                                    <TextField
                                        fullWidth
                                        label="GitHub Repository (username/repository)"
                                        value={sysUpdate.github_repo}
                                        onChange={e => setSysUpdate(s => ({ ...s, github_repo: e.target.value }))}
                                        helperText="Where deployment servers pull new releases from"
                                        sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}
                                    />
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <TextField
                                        fullWidth
                                        select
                                        SelectProps={{ native: true }}
                                        label="Server Deployment Platform"
                                        value={sysUpdate.platform}
                                        onChange={e => setSysUpdate(s => ({ ...s, platform: e.target.value }))}
                                        helperText="Determines how the web server reloads after code updates"
                                        sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}
                                    >
                                        <option value="pythonanywhere">PythonAnywhere (Touch WSGI File)</option>
                                        <option value="linux_vps">Linux VPS / Cloud (Systemd Service Restart)</option>
                                        <option value="windows_server">Windows Server / IIS Service Restart</option>
                                    </TextField>
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <Box sx={{ p: 2, border: `1px solid ${alpha(theme.palette.divider, 0.15)}`, borderRadius: '14px' }}>
                                        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                                            <Box>
                                                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Automatic Overnight Updates</Typography>
                                                <Typography variant="caption" color="text.secondary">Run scheduled code sync & safe DB upgrade while closed</Typography>
                                            </Box>
                                            <Switch
                                                checked={sysUpdate.auto_update_enabled}
                                                onChange={e => setSysUpdate(s => ({ ...s, auto_update_enabled: e.target.checked }))}
                                                color="primary"
                                            />
                                        </Box>
                                    </Box>
                                </Grid>
                                <Grid item xs={12} md={6}>
                                    <TextField
                                        fullWidth
                                        label="Overnight Auto-Update Check Time (24h format)"
                                        value={sysUpdate.auto_update_time}
                                        onChange={e => setSysUpdate(s => ({ ...s, auto_update_time: e.target.value }))}
                                        disabled={!sysUpdate.auto_update_enabled}
                                        helperText="Example: 03:00 for 3:00 AM daily check"
                                        sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}
                                    />
                                </Grid>
                            </Grid>
                            <Box sx={{ mt: 3 }}>
                                <Button
                                    type="submit"
                                    variant="contained"
                                    startIcon={sysLoading ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
                                    disabled={sysLoading}
                                    sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 4, py: 1.5 }}
                                >
                                    {sysLoading ? 'Saving...' : 'Save Update Configuration'}
                                </Button>
                            </Box>
                        </form>
                    </Section>
                </Box>
            )}
        </Box>
    );
};

export default SettingsView;
