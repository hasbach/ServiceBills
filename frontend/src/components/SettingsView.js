import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, TextField, CircularProgress,
    Avatar, Grid, Divider, Switch, alpha, useTheme, FormControlLabel,
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
                                            <TextField fullWidth label="Forwarding Alert Template" placeholder="customer_reply_alert" {...waField('template_forward_alert')}
                                                helperText="Fallback alert sent to you outside 24h window"
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

        </Box>
    );
};

export default SettingsView;
