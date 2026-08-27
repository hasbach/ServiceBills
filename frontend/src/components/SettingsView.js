import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, TextField, CircularProgress,
    Avatar, Grid, Divider, Switch, alpha, useTheme, FormControlLabel,
    Alert, Collapse, InputAdornment, IconButton, MenuItem,
    ToggleButton, ToggleButtonGroup, Tab, Tabs,
    Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
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
    Edit as EditIcon,
    Lock as LockIcon,
    Payments as PaymentsIcon,
    ContentCopy as ContentCopyIcon,
    Autorenew as AutorenewIcon,
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import ExpenseCategoryManager from './ExpenseCategoryManager.js';
import UserManagement from './UserManagement.js';
import SectorManager from './SectorManager.js';

const API_BASE_URL = process.env.REACT_APP_API_URL ?? (process.env.NODE_ENV === 'production' ? '' : 'http://localhost:5000');

// Default WhatsApp settings (outside component to avoid stale closures)
const DEFAULT_WA = {
    enabled: false, mode: 'deeplink',
    phone_number_id: '', business_account_id: '', app_id: '',
    app_secret: '', access_token: '', api_version: 'v19.0',
    template_payment_paid: 'payment_confirmation',
    template_subscription_renewed: 'subscription_renewal',
    template_payment_reminder: 'payment_reminder',
    template_current_balance: 'current_balance',
    template_forward_alert: 'customer_reply_alert',
    template_bulk_outage: 'outage_alert',
    template_bulk_maintenance: 'maintenance_alert',
    template_bulk_feature: 'feature_update',
    template_bulk_offer: 'special_offer',
    template_forward_keepalive: 'daily_checkin',
    last_forwarding_keepalive_sent_at: null,
    template_language: 'en',
    forwarding_mobile: '',
    webhook_verify_token: 'delta_net_whatsapp_secret',
    auto_reply_enabled: true,
    auto_reply_message: "your message will be redirected to customer services team, they will respond in minutes, thank you.\n\nسيتم تحويل رسالتك الى قسم خدمة الزبائن, يقومون بالرد خلال دقائق, شكرا لكم",
    // eslint-disable-next-line no-template-curly-in-string
    deeplink_msg_payment: 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!',
    // eslint-disable-next-line no-template-curly-in-string
    deeplink_msg_renewal: 'Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!',
};

// ── Section wrapper ──────────────────────────────────────────────────────────
const Section = ({ icon, title, subtitle, color, action, children }) => {
    const theme = useTheme();
    return (
        <Paper elevation={0} sx={{
            p: 3, mb: 3, borderRadius: '20px',
            background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
            border: `1px solid ${alpha(color || theme.palette.primary.main, 0.12)}`
        }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1.5, mb: 3, flexWrap: 'wrap' }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                    <Box sx={{ width: 44, height: 44, borderRadius: '14px', bgcolor: alpha(color || theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        {React.cloneElement(icon, { sx: { color: color || theme.palette.primary.main, fontSize: 22 } })}
                    </Box>
                    <Box>
                        <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>{title}</Typography>
                        {subtitle && <Typography variant="caption" color="text.secondary">{subtitle}</Typography>}
                    </Box>
                </Box>
                {action}
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
        business_name: '', address: '', mobile: '', email: '', website: '', network_mode: 'none',
        upstream_sync_automation_enabled: false
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
                website: businessSettings.website || '',
                network_mode: businessSettings.network_mode || 'none',
                upstream_sync_automation_enabled: !!businessSettings.upstream_sync_automation_enabled
            });
            if (businessSettings.logo_url) {
                const url = businessSettings.logo_url;
                // Absolute already (S3/R2 presigned URL) or the frontend's own default asset — don't double-prefix.
                setLogoPreview(url.startsWith('http') || url === '/serviceBillsLogo.png' ? url : `${API_BASE_URL}${url}`);
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
    // Locked by default so a stray keystroke/paste can't corrupt a long
    // token/ID that's just sitting in the field being viewed -- must click
    // Edit to unlock, and it re-locks after every save.
    const [waCredsEditing, setWaCredsEditing] = useState(false);

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
            setWaCredsEditing(false);
        } catch (err) {
            const detail = err?.response?.data?.error || err?.response?.data?.msg || err?.message || 'Unknown error';
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
            const detail = err?.response?.data?.error || err?.response?.data?.msg || err?.message || 'Unknown error';
            setSnackbar({ open: true, message: `Failed to link: ${detail}`, severity: 'error' });
        } finally {
            setLinkingWaba(false);
        }
    };

    const waField = (key) => ({ value: waForm[key], onChange: (e) => setWaForm(f => ({ ...f, [key]: e.target.value })) });

    // ── Tenant plan (for the Pro-gated Whish Payments tab below) ────────────────
    const [tenant, setTenant] = useState(null);
    useEffect(() => {
        apiService.tenantMe().then((r) => setTenant(r.data)).catch(() => setTenant({ plan: 'free' }));
    }, [apiService]);
    const isPro = tenant?.plan === 'pro';

    // ── Tenant Whish (customer payments) settings state ─────────────────────────
    const DEFAULT_TWS = { enabled: false, whish_channel: '', whish_secret: '', display_name_override: '', configured: false };
    const [twsForm, setTwsForm] = useState(DEFAULT_TWS);
    const [twsLoading, setTwsLoading] = useState(false);
    const [twsFetching, setTwsFetching] = useState(true);
    const [showTwsSecret, setShowTwsSecret] = useState(false);
    // Locked by default, same rationale as waCredsEditing above -- must click
    // Edit to unlock, re-locks after every save.
    const [twsCredsEditing, setTwsCredsEditing] = useState(false);

    const fetchTwsSettings = useCallback(async () => {
        setTwsFetching(true);
        try {
            const res = await apiService.tenantWhishSettings();
            if (res.data?.settings) setTwsForm(prev => ({ ...DEFAULT_TWS, ...res.data.settings }));
        } catch (e) {
            console.error('Failed to load Whish payment settings', e);
        } finally {
            setTwsFetching(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiService]);

    useEffect(() => { fetchTwsSettings(); }, [fetchTwsSettings]);

    const handleTwsSave = async () => {
        setTwsLoading(true);
        try {
            const res = await apiService.saveTenantWhishSettings(twsForm);
            if (res.data?.settings) setTwsForm(prev => ({ ...DEFAULT_TWS, ...res.data.settings }));
            setSnackbar({ open: true, message: 'Whish payment settings saved!', severity: 'success' });
            setTwsCredsEditing(false);
        } catch (err) {
            const detail = err?.response?.data?.msg || err?.response?.data?.error || err?.message || 'Unknown error';
            console.error('Whish settings save error:', err?.response || err);
            setSnackbar({ open: true, message: `Failed to save: ${detail}`, severity: 'error' });
        } finally {
            setTwsLoading(false);
        }
    };

    const twsField = (key) => ({ value: twsForm[key], onChange: (e) => setTwsForm(f => ({ ...f, [key]: e.target.value })) });

    // ── Tenant-wide public payment page link (Task 20) ──────────────────────────
    const [publicPayLink, setPublicPayLink] = useState(null); // { slug, url } | null while loading
    const [publicPayLinkLoading, setPublicPayLinkLoading] = useState(false);
    const [regenerateConfirmOpen, setRegenerateConfirmOpen] = useState(false);
    const [copied, setCopied] = useState(false);

    const fetchPublicPayLink = useCallback(async () => {
        try {
            const res = await apiService.getPublicPayLink();
            if (res.data?.slug) {
                setPublicPayLink(res.data);
            } else {
                // Lazily generate on first load -- staff shouldn't need an
                // extra manual step just to get a working link the first
                // time they open this tab. Never auto-regenerates an
                // EXISTING slug (that's the explicit, confirmed action
                // below) -- only fires when none has been generated yet.
                const gen = await apiService.regeneratePublicPayLink();
                setPublicPayLink(gen.data);
            }
        } catch (e) {
            console.error('Failed to load public payment page link', e);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiService]);

    useEffect(() => { if (isPro) fetchPublicPayLink(); }, [isPro, fetchPublicPayLink]);

    const generateOrRegeneratePublicPayLink = async () => {
        setPublicPayLinkLoading(true);
        try {
            const res = await apiService.regeneratePublicPayLink();
            setPublicPayLink(res.data);
            setSnackbar({ open: true, message: 'Public payment page link generated!', severity: 'success' });
        } catch (err) {
            const detail = err?.response?.data?.msg || err?.message || 'Unknown error';
            setSnackbar({ open: true, message: `Failed to generate link: ${detail}`, severity: 'error' });
        } finally {
            setPublicPayLinkLoading(false);
            setRegenerateConfirmOpen(false);
        }
    };

    const copyPublicPayLink = async () => {
        if (!publicPayLink?.url) return;
        try {
            await navigator.clipboard.writeText(publicPayLink.url);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (e) {
            setSnackbar({ open: true, message: 'Could not copy link.', severity: 'error' });
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
                    <Tab icon={<PaymentsIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Whish Payments" />
                    <Tab icon={<MessageIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Expense Categories" />
                    <Tab icon={<PeopleIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="User Management" />
                    <Tab icon={<LocationOnIcon sx={{ fontSize: 18 }} />} iconPosition="start" label="Sectors" />
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
                            <Grid item xs={12}>
                                <TextField fullWidth select label="Network Integration" value={bizForm.network_mode}
                                    onChange={e => setBizForm(f => ({ ...f, network_mode: e.target.value }))}
                                    helperText="How your network actually works — controls which sections (Upstream Providers / Mikrotik Servers) appear in the menu."
                                    sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}>
                                    <MenuItem value="none">None — manage subscriptions/payments only</MenuItem>
                                    <MenuItem value="upstream_bridge">Bridged — I'm a subreseller on an upstream's RADIUS portal</MenuItem>
                                    <MenuItem value="local_mikrotik">Self-hosted — I run my own Mikrotik with local PPPoE</MenuItem>
                                </TextField>
                            </Grid>
                            <Grid item xs={12}>
                                <Collapse in={bizForm.network_mode === 'upstream_bridge'}>
                                    <Alert severity="info" icon={<InfoIcon />} sx={{ borderRadius: '12px', mb: 1 }}>
                                        <FormControlLabel
                                            control={
                                                <Switch
                                                    checked={bizForm.upstream_sync_automation_enabled}
                                                    onChange={e => setBizForm(f => ({ ...f, upstream_sync_automation_enabled: e.target.checked }))}
                                                />
                                            }
                                            label="Automatically refresh upstream status daily (beta)"
                                        />
                                        <Typography variant="body2" sx={{ mt: 0.5, opacity: 0.85 }}>
                                            When on, each linked customer's upstream status/expiry is refreshed automatically once a day
                                            instead of only when someone clicks "Refresh" on their record. Read-only — this never
                                            suspends or unsuspends anyone's connection on its own, it only keeps the status shown in
                                            ServiceBills up to date. Off by default while this rolls out.
                                        </Typography>
                                    </Alert>
                                </Collapse>
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

                                <Divider sx={{ my: 3 }} />
                                {/* Mode Toggle */}
                                <Box sx={{ mb: 1 }}>
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
                            </Section>

                            {/* Deep Link Message Templates */}
                            <Collapse in={waForm.mode === 'deeplink'}>
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
                            <Collapse in={waForm.mode === 'api'}>
                                <Section icon={<ApiIcon />} title="Meta Cloud API Credentials"
                                    subtitle="Get these from your Meta for Developers dashboard (developers.facebook.com)" color={theme.palette.primary.main}
                                    action={
                                        <Button
                                            size="small"
                                            variant={waCredsEditing ? 'contained' : 'outlined'}
                                            color={waCredsEditing ? 'warning' : 'primary'}
                                            startIcon={waCredsEditing ? <LockIcon /> : <EditIcon />}
                                            onClick={() => setWaCredsEditing(e => !e)}
                                            sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}
                                        >
                                            {waCredsEditing ? 'Lock' : 'Edit'}
                                        </Button>
                                    }>
                                    <Alert severity="warning" sx={{ mb: 3, borderRadius: '12px' }}>
                                        ⚠️ This section stores your API credentials. Keep these secret — never share them. You must have a verified Meta Business account and approved message templates.
                                        {!waCredsEditing && ' Fields are locked — click Edit above to change them.'}
                                    </Alert>
                                    <Grid container spacing={2}>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="App ID" placeholder="e.g. 123456789012345" {...waField('app_id')}
                                                disabled={!waCredsEditing}
                                                helperText="From your Meta App Dashboard → App ID"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="App Secret" type={showSecret ? 'text' : 'password'} placeholder="Your app secret"
                                                {...waField('app_secret')} disabled={!waCredsEditing} helperText="Settings → Basic → App Secret"
                                                InputProps={{ endAdornment: <InputAdornment position="end"><IconButton size="small" onClick={() => setShowSecret(s => !s)}>{showSecret ? <VisibilityOffIcon /> : <VisibilityIcon />}</IconButton></InputAdornment> }}
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Phone Number ID" placeholder="e.g. 103843228738291" {...waField('phone_number_id')}
                                                disabled={!waCredsEditing}
                                                helperText="WhatsApp → API Setup → Phone Number ID"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Business Account ID (WABA ID)" placeholder="e.g. 102290129000001" {...waField('business_account_id')}
                                                disabled={!waCredsEditing}
                                                helperText="WhatsApp → API Setup → WhatsApp Business Account ID"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12}>
                                            <TextField fullWidth label="Permanent Access Token" type={showToken ? 'text' : 'password'} placeholder="EAA..." multiline={showToken} rows={showToken ? 3 : 1}
                                                {...waField('access_token')} disabled={!waCredsEditing} helperText="System User token from Business Settings → System Users → Generate Token"
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
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Payment Received Template" placeholder="payment_confirmation" {...waField('template_payment_paid')}
                                                helperText="Triggered when a payment is marked as paid"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Subscription Renewed Template" placeholder="subscription_renewal" {...waField('template_subscription_renewed')}
                                                helperText="Triggered when subscription is renewed"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Payment Reminder Template" placeholder="payment_reminder" {...waField('template_payment_reminder')}
                                                helperText="For future manual or scheduled reminders"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Current Balance Template" placeholder="current_balance" {...waField('template_current_balance')}
                                                helperText="For balance & expiry reminders"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={3}>
                                            <TextField fullWidth label="Forwarding Alert Template (unused)" placeholder="customer_reply_alert" {...waField('template_forward_alert')}
                                                helperText="Not sent by the current forwarding flow (see Daily Keep-Alive Template below) — kept only in case you revert to template-only alerts"
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
                                        When customers reply to your messages, Meta sends their replies to your webhook, which forwards the actual text/audio/video/image to your mobile number below — not just a template alert. This only works while your forwarding number has an open 24-hour WhatsApp session, which the Daily Keep-Alive Template below exists to maintain: it prompts your forwarding number once a day, and that number's own auto-reply (configured on that device, e.g. in WhatsApp Business App — not here) replies back, which is what actually opens the session.
                                    </Alert>
                                    <Grid container spacing={2}>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Forwarding Mobile Number (Business/Personal)" placeholder="e.g. 201012345678 or 010..." {...waField('forwarding_mobile')}
                                                helperText="Incoming customer replies (text, audio, video, images) are forwarded here directly — requires an open 24h session, see Daily Keep-Alive Template"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Webhook Verify Token" placeholder="delta_net_whatsapp_secret" {...waField('webhook_verify_token')}
                                                helperText="Use this exact secret token in Meta Developer Console when configuring your Webhook"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField fullWidth label="Daily Keep-Alive Template" placeholder="daily_checkin" {...waField('template_forward_keepalive')}
                                                helperText="Sent to the forwarding number once a day to prompt its own auto-reply and keep the 24h session open"
                                                sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                        </Grid>
                                        <Grid item xs={12} md={6} sx={{ display: 'flex', alignItems: 'center' }}>
                                            <Typography variant="body2" color="text.secondary">
                                                {waForm.last_forwarding_keepalive_sent_at
                                                    ? `Last keep-alive sent: ${waForm.last_forwarding_keepalive_sent_at}`
                                                    : 'Keep-alive not sent yet — runs automatically once a day.'}
                                            </Typography>
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
                                        <Grid item xs={12}>
                                            <Divider sx={{ my: 2 }} />
                                            <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1, color: theme.palette.primary.main }}>
                                                🤖 Automated Customer Acknowledgment (Bilingual)
                                            </Typography>
                                            <FormControlLabel
                                                control={<Switch checked={Boolean(waForm.auto_reply_enabled)} onChange={(e) => setWaForm(f => ({ ...f, auto_reply_enabled: e.target.checked }))} color="primary" />}
                                                label="Send instant auto-reply acknowledgment when a customer replies to your bot"
                                                sx={{ mb: 1 }}
                                            />
                                            {waForm.auto_reply_enabled && (
                                                <TextField fullWidth multiline rows={3} label="Auto-Reply Message" {...waField('auto_reply_message')}
                                                    helperText="This message is sent instantly to the customer when they message or reply to your bot (debounced to once every 15 minutes per customer)"
                                                    sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                            )}
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

            {/* ── Tab 2: Whish Payments (tenant-facing customer payments) ── */}
            {tab === 2 && (
                <Box>
                    {twsFetching || !tenant ? (
                        <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}><CircularProgress /></Box>
                    ) : !isPro ? (
                        <Section icon={<PaymentsIcon />} title="Whish Payments" subtitle="Let your customers pay you directly via Whish" color={theme.palette.warning.main}>
                            <Alert severity="warning" icon={<LockIcon />} sx={{ borderRadius: '12px' }}>
                                This feature requires the <strong>Pro</strong> plan. Upgrade from the Billing page to let your
                                customers pay their invoices directly via Whish, using your own Whish merchant account.
                            </Alert>
                        </Section>
                    ) : (
                        <>
                            <Section icon={<PaymentsIcon />} title="Whish Payments" subtitle="Accept payments from your own customers via Whish" color={theme.palette.primary.main}
                                action={
                                    <Button
                                        size="small"
                                        variant={twsCredsEditing ? 'contained' : 'outlined'}
                                        color={twsCredsEditing ? 'warning' : 'primary'}
                                        startIcon={twsCredsEditing ? <LockIcon /> : <EditIcon />}
                                        onClick={() => setTwsCredsEditing(e => !e)}
                                        sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}
                                    >
                                        {twsCredsEditing ? 'Lock' : 'Edit'}
                                    </Button>
                                }>
                                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2, mb: 3 }}>
                                    <Box>
                                        <Typography variant="body1" sx={{ fontWeight: 600 }}>Enable Whish Payments</Typography>
                                        <Typography variant="body2" color="text.secondary">Let customers pay you directly using the credentials below</Typography>
                                    </Box>
                                    <Switch checked={twsForm.enabled} onChange={e => setTwsForm(f => ({ ...f, enabled: e.target.checked }))} />
                                </Box>
                                <Alert severity="warning" sx={{ mb: 3, borderRadius: '12px' }}>
                                    ⚠️ This section stores your own Whish merchant credentials. Keep these secret — never share them.
                                    {!twsCredsEditing && ' Fields are locked — click Edit above to change them.'}
                                </Alert>
                                <Grid container spacing={2}>
                                    <Grid item xs={12} md={6}>
                                        <TextField fullWidth label="Whish Channel" type={showTwsSecret ? 'text' : 'password'} placeholder="Your Whish channel ID"
                                            {...twsField('whish_channel')} disabled={!twsCredsEditing}
                                            InputProps={{ endAdornment: <InputAdornment position="end"><IconButton size="small" onClick={() => setShowTwsSecret(s => !s)}>{showTwsSecret ? <VisibilityOffIcon /> : <VisibilityIcon />}</IconButton></InputAdornment> }}
                                            sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                    </Grid>
                                    <Grid item xs={12} md={6}>
                                        <TextField fullWidth label="Whish Secret" type={showTwsSecret ? 'text' : 'password'} placeholder="Your Whish secret"
                                            {...twsField('whish_secret')} disabled={!twsCredsEditing}
                                            sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                    </Grid>
                                    <Grid item xs={12}>
                                        <TextField fullWidth label="Display Name (optional)" placeholder="Shown to your customers instead of your internal business name"
                                            {...twsField('display_name_override')} disabled={!twsCredsEditing}
                                            helperText="Not shown on the payment page yet — captured for future use"
                                            sx={{ '& .MuiOutlinedInput-root': { borderRadius: '12px' } }} />
                                    </Grid>
                                </Grid>
                            </Section>
                            <Button variant="contained" startIcon={twsLoading ? <CircularProgress size={18} color="inherit" /> : <SaveIcon />}
                                onClick={handleTwsSave} disabled={twsLoading}
                                sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600, px: 4, py: 1.5 }}>
                                {twsLoading ? 'Saving…' : 'Save Whish Payment Settings'}
                            </Button>

                            <Box sx={{ mt: 3 }} />
                            <Section icon={<LinkIcon />} title="Public Payment Page" subtitle="One link, safe to hand out to anyone -- customers self-identify by phone and pay whatever they owe" color={theme.palette.secondary.main}>
                                {!publicPayLink ? (
                                    <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}><CircularProgress size={24} /></Box>
                                ) : (
                                    <>
                                        <TextField
                                            fullWidth label="Payment page link" value={publicPayLink.url || ''} InputProps={{ readOnly: true }}
                                            sx={{ mb: 2, '& .MuiOutlinedInput-root': { borderRadius: '12px' } }}
                                        />
                                        <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
                                            <Button variant="outlined" startIcon={<ContentCopyIcon />} onClick={copyPublicPayLink}
                                                sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
                                                {copied ? 'Copied!' : 'Copy Link'}
                                            </Button>
                                            <Button variant="outlined" color="warning" startIcon={<AutorenewIcon />}
                                                onClick={() => setRegenerateConfirmOpen(true)} disabled={publicPayLinkLoading}
                                                sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
                                                Regenerate Link
                                            </Button>
                                        </Box>
                                        <Alert severity="info" sx={{ mt: 2, borderRadius: '12px' }}>
                                            Regenerating breaks the current link -- anyone who saved or bookmarked it will no longer be able to pay through it.
                                        </Alert>
                                    </>
                                )}
                            </Section>
                        </>
                    )}
                </Box>
            )}

            <Dialog open={regenerateConfirmOpen} onClose={() => setRegenerateConfirmOpen(false)}>
                <DialogTitle>Regenerate the public payment link?</DialogTitle>
                <DialogContent>
                    <DialogContentText>
                        This replaces your current public payment page link with a brand-new one. The old link will stop
                        working immediately for anyone who has it saved, bookmarked, or shared. This can't be undone.
                    </DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setRegenerateConfirmOpen(false)} disabled={publicPayLinkLoading}>Cancel</Button>
                    <Button onClick={generateOrRegeneratePublicPayLink} color="warning" variant="contained" disabled={publicPayLinkLoading}>
                        {publicPayLinkLoading ? 'Regenerating…' : 'Regenerate'}
                    </Button>
                </DialogActions>
            </Dialog>

            {/* ── Tab 3: Expense Categories ── */}
            {tab === 3 && (
                <Section icon={<MessageIcon />} title="Expense Categories" subtitle="Manage the categories used to classify expenses" color={theme.palette.warning.main}>
                    <ExpenseCategoryManager />
                </Section>
            )}

            {/* ── Tab 4: User Management ── */}
            {tab === 4 && (
                <UserManagement />
            )}

            {/* Tab 5: Sectors */}
            {tab === 5 && (
                <SectorManager />
            )}

        </Box>
    );
};

export default SettingsView;
