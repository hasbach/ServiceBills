import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, Chip, Alert, CircularProgress, Collapse,
    Dialog, DialogTitle, DialogContent, DialogContentText, DialogActions,
    Grid, Divider, Link, Stack, TextField, alpha, useTheme,
} from '@mui/material';
import {
    WhatsApp as WhatsAppIcon,
    PhoneIphone as PhoneIphoneIcon,
    AddIcCall as AddIcCallIcon,
    Settings as SettingsIcon,
    Lock as LockIcon,
    Refresh as RefreshIcon,
    LinkOff as LinkOffIcon,
    Replay as ReplayIcon,
    History as HistoryIcon,
    ExpandMore as ExpandMoreIcon,
    ExpandLess as ExpandLessIcon,
    Description as DescriptionIcon,
    OpenInNew as OpenInNewIcon,
    Send as SendIcon,
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import StandardTemplatesDialog from './StandardTemplatesDialog.js';

// Automatic WhatsApp connection (specs/001-whatsapp-auto-connect, T021/T030/T038).
// Methods: Meta Embedded Signup v4, either "coexistence" (keep the WhatsApp
// Business app number on the phone) or "embedded" (a new API-only number),
// plus the existing manual credential form, which SettingsView renders.

const FB_SDK_SRC = 'https://connect.facebook.net/en_US/sdk.js';
const WA_MANAGER_URL = 'https://business.facebook.com/wa/manage/';
// Meta's auth code lives 30 s; whichever of the FB.login callback and the
// postMessage arrives first waits at most this long for the other.
const PAIR_WAIT_MS = 10000;
// After FB.login returns without a code, give a trailing CANCEL message this
// long to arrive so we can say which step the tenant stopped at.
const CANCEL_GRACE_MS = 1500;
const SUCCESS_EVENTS = ['FINISH', 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING', 'FINISH_ONLY_WABA'];

const METHOD_LABEL = {
    coexistence: 'WhatsApp Business app (coexistence)',
    embedded: 'WhatsApp Business API (new number)',
    manual: 'Manual configuration',
};

const STATUS_CHIP = {
    connected: { label: 'Connected', color: 'success' },
    needs_attention: { label: 'Needs attention', color: 'warning' },
    disconnected: { label: 'Disconnected', color: 'default' },
};

const QUALITY_COLOR = { GREEN: 'success', YELLOW: 'warning', RED: 'error' };

const errText = (err, fallback) => err?.response?.data?.error || err?.response?.data?.msg || err?.message || fallback;

// ConnectionView stamps are ISO with a trailing Z; event stamps may be the
// API's usual zone-less UTC. Both render in the viewer's timezone.
function fmtTime(stamp) {
    if (!stamp) return '—';
    let s = String(stamp).replace(' ', 'T');
    if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(s)) s += 'Z';
    const ms = Date.parse(s);
    return Number.isNaN(ms) ? String(stamp) : new Date(ms).toLocaleString();
}

function fmtTier(tier) {
    if (!tier) return '—';
    const m = /^TIER_(\w+)$/.exec(tier);
    if (!m) return tier;
    if (m[1] === 'UNLIMITED') return 'Unlimited';
    return `${m[1]} customers / 24 h`;
}

function isFacebookOrigin(origin) {
    try {
        const host = new URL(origin).hostname;
        return host === 'facebook.com' || host.endsWith('.facebook.com');
    } catch {
        return false;
    }
}

function clearSessionTimers(s) {
    if (!s) return;
    if (s.pairTimer) clearTimeout(s.pairTimer);
    if (s.cancelTimer) clearTimeout(s.cancelTimer);
    s.pairTimer = null;
    s.cancelTimer = null;
}

// Load the Facebook JS SDK once, on demand, and init it for the platform app.
let sdkPromise = null;
function loadFacebookSdk(appId, version) {
    if (sdkPromise) return sdkPromise;
    sdkPromise = new Promise((resolve, reject) => {
        let settled = false;
        const init = () => {
            if (settled) return;
            try {
                window.FB.init({ appId, version, xfbml: false, autoLogAppEvents: false });
                settled = true;
                resolve(window.FB);
            } catch (e) {
                settled = true;
                reject(e);
            }
        };
        const fail = (msg) => {
            if (settled) return;
            settled = true;
            sdkPromise = null;
            reject(new Error(msg));
        };
        if (window.FB) { init(); return; }
        window.fbAsyncInit = init;
        const script = document.createElement('script');
        script.src = FB_SDK_SRC;
        script.async = true;
        script.defer = true;
        script.crossOrigin = 'anonymous';
        script.onerror = () => {
            script.remove();
            fail('Could not load the Facebook sign-in script. Check your internet connection or ad blocker, then try again.');
        };
        document.body.appendChild(script);
        setTimeout(() => fail('Loading the Facebook sign-in script timed out. Please try again.'), 20000);
    });
    return sdkPromise;
}

const MethodCard = ({ icon, title, description, selected, current, locked, lockText, disabled, onClick, color }) => {
    const theme = useTheme();
    const c = color || theme.palette.primary.main;
    return (
        <Paper
            variant="outlined"
            role="button"
            tabIndex={locked || disabled ? -1 : 0}
            aria-disabled={locked || disabled}
            onClick={locked || disabled ? undefined : onClick}
            onKeyDown={(e) => { if (!locked && !disabled && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onClick(); } }}
            sx={{
                p: 2, height: '100%', borderRadius: '16px', cursor: locked || disabled ? 'default' : 'pointer',
                borderColor: selected ? c : undefined, borderWidth: selected ? 2 : 1,
                bgcolor: selected ? alpha(c, 0.05) : 'background.paper',
                opacity: locked ? 0.75 : 1,
                transition: 'border-color .15s, background-color .15s',
                '&:hover': locked || disabled ? {} : { borderColor: c },
                display: 'flex', flexDirection: 'column', gap: 1,
            }}
        >
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Box sx={{ width: 36, height: 36, borderRadius: '10px', bgcolor: alpha(c, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                    {locked ? <LockIcon sx={{ color: 'text.secondary', fontSize: 20 }} /> : React.cloneElement(icon, { sx: { color: c, fontSize: 20 } })}
                </Box>
                <Typography variant="subtitle2" sx={{ fontWeight: 700, lineHeight: 1.25, flex: 1 }}>{title}</Typography>
                {current && <Chip size="small" label="Current" color="primary" variant="outlined" />}
            </Box>
            <Typography variant="body2" color="text.secondary">{description}</Typography>
            {locked && (
                <Typography variant="caption" sx={{ color: 'warning.dark', fontWeight: 600 }}>{lockText}</Typography>
            )}
        </Paper>
    );
};

const Field = ({ label, children }) => (
    <Box sx={{ minWidth: 0 }}>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{label}</Typography>
        <Typography component="div" variant="body2" sx={{ fontWeight: 600, wordBreak: 'break-word' }}>{children || '—'}</Typography>
    </Box>
);

/**
 * Props:
 *  - connection: ConnectionView from GET /api/whatsapp-settings (fallback until /connect/status answers)
 *  - manualVisible: whether SettingsView currently shows the manual credential form
 *  - onSelectManual(bool): reveal / hide the manual form
 *  - onAutomaticAvailable(bool): tells SettingsView whether the automatic methods exist at all
 *  - onConnectionChange(connection): called after connect / refresh / disconnect
 */
const WhatsAppConnectCard = ({ connection: connectionProp, manualVisible, onSelectManual, onAutomaticAvailable, onConnectionChange }) => {
    const { apiService } = useAppContext();
    const theme = useTheme();

    const [config, setConfig] = useState(null);
    const [configLoading, setConfigLoading] = useState(true);
    const [connection, setConnection] = useState(connectionProp || null);
    const [events, setEvents] = useState([]);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [busy, setBusy] = useState(null); // 'connecting' | 'refreshing' | 'disconnecting'
    const [flowMsg, setFlowMsg] = useState(null); // { severity, text, hint? }
    const [sdkState, setSdkState] = useState('idle'); // idle | loading | ready | error
    const [sdkError, setSdkError] = useState('');
    const [launchMethod, setLaunchMethod] = useState(null); // method shown in the pre-launch dialog
    const [launchOpen, setLaunchOpen] = useState(false);
    const [confirmManualOpen, setConfirmManualOpen] = useState(false);
    const [confirmDisconnectOpen, setConfirmDisconnectOpen] = useState(false);
    const [templatesOpen, setTemplatesOpen] = useState(false);
    const [hasStandardTemplates, setHasStandardTemplates] = useState(null); // null = unknown
    const [testOpen, setTestOpen] = useState(false);
    const [testTo, setTestTo] = useState('');
    const [testResult, setTestResult] = useState(null); // { severity, text }

    const mountedRef = useRef(true);
    const sessionRef = useRef(null);
    const onConnectionChangeRef = useRef(onConnectionChange);
    onConnectionChangeRef.current = onConnectionChange;

    useEffect(() => {
        if (connectionProp) setConnection(prev => prev || connectionProp);
    }, [connectionProp]);

    const applyConnection = useCallback((conn, notify = true) => {
        if (!conn || !mountedRef.current) return;
        setConnection(conn);
        if (notify && onConnectionChangeRef.current) onConnectionChangeRef.current(conn);
    }, []);

    const loadStatus = useCallback(async () => {
        try {
            const res = await apiService.getWaConnectStatus();
            if (!mountedRef.current) return;
            if (res.data?.connection) setConnection(res.data.connection);
            setEvents(Array.isArray(res.data?.events) ? res.data.events : []);
        } catch {
            // Route missing (older backend) or feature off: keep the settings copy.
        }
    }, [apiService]);

    const loadTemplatesState = useCallback(async () => {
        try {
            const res = await apiService.getStandardTemplates();
            if (!mountedRef.current) return;
            const list = res.data?.templates || [];
            setHasStandardTemplates(list.some(t => t.existing));
        } catch {
            if (mountedRef.current) setHasStandardTemplates(null);
        }
    }, [apiService]);

    // ── Mount: config + status ───────────────────────────────────────────────
    useEffect(() => {
        mountedRef.current = true;
        (async () => {
            try {
                const res = await apiService.getWaConnectConfig();
                if (!mountedRef.current) return;
                setConfig(res.data || null);
                if (onAutomaticAvailable) onAutomaticAvailable(Boolean(res.data?.automatic_available));
            } catch {
                // 404 = feature disabled (no platform Meta config, or on-prem).
                if (!mountedRef.current) return;
                setConfig({ automatic_available: false });
                if (onAutomaticAvailable) onAutomaticAvailable(false);
            } finally {
                if (mountedRef.current) setConfigLoading(false);
            }
        })();
        loadStatus();
        return () => { mountedRef.current = false; };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    const isLive = connection && connection.status !== 'disconnected';
    const isAutomatic = connection && connection.method !== 'manual';
    const automaticLive = isLive && isAutomatic;

    useEffect(() => {
        if (isLive) loadTemplatesState();
    }, [isLive, loadTemplatesState]);

    // ── Embedded Signup session handling ─────────────────────────────────────
    const endSession = useCallback((msg) => {
        const s = sessionRef.current;
        clearSessionTimers(s);
        sessionRef.current = null;
        if (!mountedRef.current) return;
        setBusy(null);
        if (msg) setFlowMsg(msg);
    }, []);

    const notCompleted = useCallback((s) => {
        const step = s?.cancel?.current_step;
        const reason = s?.cancel?.error_message;
        const parts = ['Connection not completed.'];
        if (step) parts.push(`You stopped at: ${String(step).replace(/_/g, ' ').toLowerCase()}.`);
        if (reason) parts.push(reason);
        parts.push('Your previous WhatsApp settings are unchanged.');
        endSession({
            severity: 'warning',
            text: parts.join(' '),
            hint: !s?.anyMessage
                ? 'If no Meta window appeared, your browser may have blocked the pop-up. Allow pop-ups for this site and try again.'
                : null,
        });
    }, [endSession]);

    const submitIfReady = useCallback(async () => {
        const s = sessionRef.current;
        if (!s || s.submitting) return;
        if (s.code && s.success) {
            s.submitting = true;
            clearSessionTimers(s);
            try {
                const res = await apiService.completeWaConnect({
                    method: s.method,
                    code: s.code,
                    waba_id: s.success.waba_id,
                    phone_number_id: s.success.phone_number_id,
                    business_id: s.success.business_id,
                    es_event: s.success.event,
                });
                if (sessionRef.current !== s) return;
                applyConnection(res.data?.connection);
                endSession({ severity: 'success', text: `WhatsApp connected${res.data?.connection?.display_phone_number ? `: ${res.data.connection.display_phone_number}` : ''}.` });
                loadStatus();
                loadTemplatesState();
            } catch (err) {
                if (sessionRef.current !== s) return;
                const text = errText(err, 'Connecting WhatsApp failed.');
                const hint = s.method === 'embedded'
                    ? 'If this number is already used in a WhatsApp app, try "Use my WhatsApp Business app number" or a different number.'
                    : (s.method === 'coexistence' ? 'If this number is not eligible, try "New number (WhatsApp Business API)" instead.' : null);
                endSession({ severity: 'error', text: `${text} Your previous WhatsApp settings are unchanged.`, hint });
            }
            return;
        }
        // One half arrived: wait (once) for the other.
        if ((s.code || s.success) && !s.pairTimer) {
            s.pairTimer = setTimeout(() => {
                if (sessionRef.current !== s || s.submitting) return;
                endSession({
                    severity: 'error',
                    text: s.code
                        ? 'Meta did not send the WhatsApp account details back. Please try again. Your previous settings are unchanged.'
                        : 'Meta did not confirm the sign-in. Please try again. Your previous settings are unchanged.',
                });
            }, PAIR_WAIT_MS);
        }
    }, [apiService, applyConnection, endSession, loadStatus, loadTemplatesState]);

    // The listener is attached once; it always calls the latest handler.
    const messageHandlerRef = useRef(null);
    messageHandlerRef.current = (event) => {
        if (!isFacebookOrigin(event.origin)) return;
        let data = event.data;
        if (typeof data === 'string') {
            try { data = JSON.parse(data); } catch { return; }
        }
        if (!data || typeof data !== 'object' || data.type !== 'WA_EMBEDDED_SIGNUP') return;
        const s = sessionRef.current;
        if (!s) return;
        s.anyMessage = true;
        const payload = data.data || {};
        if (SUCCESS_EVENTS.includes(data.event)) {
            s.success = {
                event: data.event,
                phone_number_id: payload.phone_number_id,
                waba_id: payload.waba_id,
                business_id: payload.business_id,
            };
            submitIfReady();
        } else if (data.event === 'CANCEL' || data.event === 'ERROR') {
            s.cancel = payload;
            // If FB.login already returned without a code, finish now.
            if (s.loginReturned && !s.code) notCompleted(s);
        }
    };
    useEffect(() => {
        const onMessage = (event) => { if (messageHandlerRef.current) messageHandlerRef.current(event); };
        window.addEventListener('message', onMessage);
        return () => {
            window.removeEventListener('message', onMessage);
            clearSessionTimers(sessionRef.current);
            sessionRef.current = null;
        };
    }, []);

    const prepareSdk = useCallback(() => {
        if (!config?.automatic_available || config?.plan_allows_api === false) return;
        if (sdkState === 'loading' || sdkState === 'ready') return;
        setSdkState('loading');
        setSdkError('');
        loadFacebookSdk(config.app_id, config.graph_version)
            .then(() => { if (mountedRef.current) setSdkState('ready'); })
            .catch((e) => {
                if (!mountedRef.current) return;
                setSdkState('error');
                setSdkError(e.message || 'Could not load the Facebook sign-in script.');
            });
    }, [config, sdkState]);

    // Must run inside the click handler (user gesture) so the Meta pop-up
    // isn't blocked; that's why the SDK is preloaded when the dialog opens.
    const launchFlow = (method) => {
        setLaunchOpen(false);
        setFlowMsg(null);
        const FB = window.FB;
        if (!FB || sdkState !== 'ready') {
            setFlowMsg({ severity: 'error', text: sdkError || 'The Facebook sign-in script is not ready yet. Please try again.' });
            return;
        }
        const coex = method === 'coexistence';
        const s = { method, code: null, success: null, cancel: null, anyMessage: false, loginReturned: false, submitting: false, pairTimer: null, cancelTimer: null };
        clearSessionTimers(sessionRef.current);
        sessionRef.current = s;
        setBusy('connecting');
        try {
            FB.login((response) => {
                if (sessionRef.current !== s) return;
                s.loginReturned = true;
                const code = response?.authResponse?.code;
                if (code) {
                    s.code = code;
                    submitIfReady();
                } else if (!s.success) {
                    // Closed or denied. Wait briefly for a CANCEL message with the step.
                    s.cancelTimer = setTimeout(() => {
                        if (sessionRef.current === s && !s.code) notCompleted(s);
                    }, CANCEL_GRACE_MS);
                } else {
                    endSession({ severity: 'error', text: 'Meta finished the setup but did not return an authorization code. Please try again. Your previous settings are unchanged.' });
                }
            }, {
                config_id: coex ? (config.es_coex_config_id || config.es_config_id) : config.es_config_id,
                response_type: 'code',
                override_default_response_type: true,
                extras: { setup: {}, ...(coex ? (config.coex_extras || {}) : {}) },
            });
        } catch (e) {
            endSession({ severity: 'error', text: e?.message || 'Could not open the Meta sign-in window.', hint: 'Allow pop-ups for this site and try again.' });
        }
    };

    // ── Actions ──────────────────────────────────────────────────────────────
    const chooseAutomatic = (method) => {
        prepareSdk();
        setLaunchMethod(method);
        setLaunchOpen(true);
    };

    const chooseManual = () => {
        if (automaticLive) {
            setConfirmManualOpen(true);
        } else if (onSelectManual) {
            onSelectManual(true);
        }
    };

    const doDisconnect = async ({ toManual }) => {
        setBusy('disconnecting');
        setFlowMsg(null);
        try {
            // `next_method` is advisory; see the report on the contract gap.
            const res = await apiService.disconnectWa(toManual ? { next_method: 'manual' } : {});
            applyConnection(res.data?.connection);
            setFlowMsg({ severity: 'info', text: toManual
                ? 'The automatic connection was disconnected. Enter your own Meta app credentials below.'
                : 'WhatsApp disconnected. Automatic sending stopped; message history is kept.' });
            if (toManual && onSelectManual) onSelectManual(true);
            loadStatus();
        } catch (err) {
            setFlowMsg({ severity: 'error', text: errText(err, 'Disconnecting failed.') });
        } finally {
            if (mountedRef.current) setBusy(null);
        }
    };

    const doRefresh = async () => {
        setBusy('refreshing');
        setFlowMsg(null);
        try {
            const res = await apiService.refreshWaConnect();
            applyConnection(res.data?.connection);
            loadStatus();
        } catch (err) {
            setFlowMsg({ severity: 'error', text: errText(err, 'Checking the connection failed.') });
        } finally {
            if (mountedRef.current) setBusy(null);
        }
    };

    const doSendTest = async () => {
        setBusy('testing');
        setTestResult(null);
        try {
            const res = await apiService.sendWaTestMessage({ to: testTo });
            setTestResult({ severity: 'success', text: res.data?.message || 'Test message sent.' });
        } catch (err) {
            setTestResult({ severity: 'error', text: errText(err, 'Sending the test message failed.') });
            loadStatus();
        } finally {
            if (mountedRef.current) setBusy(null);
        }
    };

    // ── Render ───────────────────────────────────────────────────────────────
    if (configLoading) {
        return (
            <Paper elevation={0} sx={{ p: 3, mb: 3, borderRadius: '20px', border: `1px solid ${alpha('#25D366', 0.12)}`, display: 'flex', justifyContent: 'center' }}>
                <CircularProgress size={28} />
            </Paper>
        );
    }

    const automaticAvailable = Boolean(config?.automatic_available);
    const coexAvailable = automaticAvailable && Boolean(config?.coexistence_available);
    const planAllows = config?.plan_allows_api !== false;
    const lockText = 'Not included in your plan. Upgrade to connect automatically.';
    const statusChip = STATUS_CHIP[connection?.status] || STATUS_CHIP.disconnected;
    const showStatus = Boolean(connection) && (isAutomatic || isLive);
    const paymentWarn = automaticLive && ['missing', 'unknown'].includes(connection?.payment_method_status);
    const manualCurrent = connection?.method === 'manual' && isLive;
    const working = Boolean(busy);

    return (
        <Paper elevation={0} sx={{
            p: { xs: 2, sm: 3 }, mb: 3, borderRadius: '20px',
            background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
            border: `1px solid ${alpha('#25D366', 0.12)}`,
        }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
                <Box sx={{ width: 44, height: 44, borderRadius: '14px', bgcolor: alpha('#25D366', 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                    <WhatsAppIcon sx={{ color: '#25D366', fontSize: 22 }} />
                </Box>
                <Box sx={{ minWidth: 0 }}>
                    <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>WhatsApp connection</Typography>
                    <Typography variant="caption" color="text.secondary">How this account sends WhatsApp messages automatically</Typography>
                </Box>
            </Box>

            {/* ── Status ── */}
            {showStatus && (
                <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2 }, mb: 2, borderRadius: '16px' }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1.5 }}>
                        <Chip size="small" label={statusChip.label} color={statusChip.color}
                            sx={statusChip.color === 'warning' ? { fontWeight: 700 } : undefined} />
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>{METHOD_LABEL[connection.method] || connection.method}</Typography>
                        <Box sx={{ flex: 1 }} />
                        {isAutomatic && (
                            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
                                {isLive && (
                                    <Button size="small" variant="outlined" startIcon={busy === 'refreshing' ? <CircularProgress size={14} /> : <RefreshIcon />}
                                        onClick={doRefresh} disabled={working} sx={{ textTransform: 'none', borderRadius: '10px' }}>
                                        Refresh
                                    </Button>
                                )}
                                {planAllows && automaticAvailable && (connection.method !== 'coexistence' || coexAvailable) && (
                                    <Button size="small" variant={connection.status === 'connected' ? 'outlined' : 'contained'} color={connection.status === 'needs_attention' ? 'warning' : 'primary'}
                                        startIcon={<ReplayIcon />} onClick={() => chooseAutomatic(connection.method)}
                                        disabled={working} sx={{ textTransform: 'none', borderRadius: '10px' }}>
                                        Reconnect
                                    </Button>
                                )}
                                {connection.status === 'connected' && (
                                    <Button size="small" variant="outlined" startIcon={<SendIcon />}
                                        onClick={() => { setTestResult(null); setTestOpen(true); }} disabled={working}
                                        sx={{ textTransform: 'none', borderRadius: '10px' }}>
                                        Send test message
                                    </Button>
                                )}
                                {isLive && (
                                    <Button size="small" variant="outlined" color="error" startIcon={busy === 'disconnecting' ? <CircularProgress size={14} /> : <LinkOffIcon />}
                                        onClick={() => setConfirmDisconnectOpen(true)} disabled={working} sx={{ textTransform: 'none', borderRadius: '10px' }}>
                                        Disconnect
                                    </Button>
                                )}
                            </Stack>
                        )}
                    </Box>

                    {connection.status === 'needs_attention' && (
                        <Alert severity="warning" sx={{ mb: 1.5, borderRadius: '12px' }}>
                            {connection.status_reason || 'The connection to Meta stopped working.'} Automatic messages are paused until you reconnect.
                        </Alert>
                    )}

                    <Grid container spacing={1.5}>
                        <Grid item xs={6} sm={4}><Field label="Number">{connection.display_phone_number}</Field></Grid>
                        <Grid item xs={6} sm={4}><Field label="Display name">{connection.verified_name}</Field></Grid>
                        <Grid item xs={12} sm={4}><Field label="Business account">{connection.meta_business_name}</Field></Grid>
                        <Grid item xs={6} sm={4}>
                            <Field label="Quality">
                                {connection.quality_rating
                                    ? <Chip size="small" label={connection.quality_rating} color={QUALITY_COLOR[connection.quality_rating] || 'default'} variant="outlined" />
                                    : null}
                            </Field>
                        </Grid>
                        <Grid item xs={6} sm={4}><Field label="Messaging limit">{connection.messaging_limit_tier ? fmtTier(connection.messaging_limit_tier) : null}</Field></Grid>
                        <Grid item xs={12} sm={4}>
                            <Field label="Payment method in Meta">
                                {{ present: 'Set up', missing: 'Missing', unknown: 'Check in WhatsApp Manager' }[connection.payment_method_status] || null}
                            </Field>
                        </Grid>
                        <Grid item xs={12} sm={8}>
                            <Field label="Connected">
                                {connection.connected_at ? `${fmtTime(connection.connected_at)}${connection.connected_by ? ` by ${connection.connected_by}` : ''}` : null}
                            </Field>
                        </Grid>
                        <Grid item xs={12} sm={4}><Field label="Last checked">{connection.last_health_check_at ? fmtTime(connection.last_health_check_at) : null}</Field></Grid>
                    </Grid>

                    {paymentWarn && (
                        <Alert severity="warning" sx={{ mt: 1.5, borderRadius: '12px' }}
                            action={
                                <Button color="inherit" size="small" endIcon={<OpenInNewIcon fontSize="small" />} href={WA_MANAGER_URL}
                                    target="_blank" rel="noopener noreferrer" sx={{ textTransform: 'none', whiteSpace: 'nowrap' }}>
                                    Open
                                </Button>
                            }>
                            {connection.payment_method_status === 'missing'
                                ? 'No payment method is set up in Meta. Payment confirmations and reminders will not be delivered until you add one in WhatsApp Manager.'
                                : 'We could not confirm a payment method in Meta. Check WhatsApp Manager: without one, payment confirmations and reminders are not delivered.'}
                        </Alert>
                    )}

                    {isLive && (
                        <Box sx={{ mt: 1.5 }}>
                            {hasStandardTemplates === false && (
                                <Alert severity="info" sx={{ mb: 1, borderRadius: '12px' }}>
                                    Automatic messages need templates approved by Meta. Submit the standard set in one step; you can edit the wording first.
                                </Alert>
                            )}
                            <Button size="small" variant={hasStandardTemplates === false ? 'contained' : 'text'} startIcon={<DescriptionIcon />}
                                onClick={() => setTemplatesOpen(true)} sx={{ textTransform: 'none', borderRadius: '10px' }}>
                                Submit standard templates
                            </Button>
                        </Box>
                    )}

                    {events.length > 0 && (
                        <Box sx={{ mt: 1.5 }}>
                            <Divider sx={{ mb: 1 }} />
                            <Button size="small" startIcon={<HistoryIcon />} endIcon={historyOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                                onClick={() => setHistoryOpen(o => !o)} sx={{ textTransform: 'none' }}>
                                Connection history ({events.length})
                            </Button>
                            <Collapse in={historyOpen} unmountOnExit>
                                <Stack spacing={1} sx={{ mt: 1 }}>
                                    {events.map((ev, i) => (
                                        <Box key={ev.id ?? i} sx={{ p: 1, borderRadius: '10px', bgcolor: alpha(theme.palette.text.primary, 0.03) }}>
                                            <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'baseline' }}>
                                                <Typography variant="body2" sx={{ fontWeight: 600 }}>{String(ev.event || '').replace(/_/g, ' ')}</Typography>
                                                {ev.to_status && <Typography variant="caption" color="text.secondary">→ {ev.to_status.replace(/_/g, ' ')}</Typography>}
                                                <Box sx={{ flex: 1 }} />
                                                <Typography variant="caption" color="text.secondary">{fmtTime(ev.created_at)}</Typography>
                                            </Box>
                                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                                                {ev.user || ev.username || ev.by || ev.source || ''}{ev.detail ? ` · ${ev.detail}` : ''}
                                            </Typography>
                                        </Box>
                                    ))}
                                </Stack>
                            </Collapse>
                        </Box>
                    )}
                </Paper>
            )}

            {flowMsg && (
                <Alert severity={flowMsg.severity} onClose={() => setFlowMsg(null)} sx={{ mb: 2, borderRadius: '12px' }}>
                    {flowMsg.text}
                    {flowMsg.hint && <Box sx={{ mt: 0.5 }}>{flowMsg.hint}</Box>}
                </Alert>
            )}
            {busy === 'connecting' && (
                <Alert severity="info" icon={<CircularProgress size={18} />} sx={{ mb: 2, borderRadius: '12px' }}>
                    Finish the steps in the Meta window. If no window opened, allow pop-ups for this site and try again.
                </Alert>
            )}

            {/* ── Method choice ── */}
            <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                {showStatus ? 'Change connection method' : 'Choose how to connect'}
            </Typography>
            <Grid container spacing={1.5}>
                {coexAvailable && (
                    <Grid item xs={12} md={4}>
                        <MethodCard
                            icon={<PhoneIphoneIcon />}
                            color="#25D366"
                            title="Use my WhatsApp Business app number"
                            description="Keep chatting from the WhatsApp Business app on your phone; automatic messages go out from the same number."
                            current={isLive && connection?.method === 'coexistence'}
                            selected={launchOpen && launchMethod === 'coexistence'}
                            locked={!planAllows}
                            lockText={lockText}
                            disabled={working}
                            onClick={() => chooseAutomatic('coexistence')}
                        />
                    </Grid>
                )}
                {automaticAvailable && (
                    <Grid item xs={12} md={coexAvailable ? 4 : 6}>
                        <MethodCard
                            icon={<AddIcCallIcon />}
                            title="New number (WhatsApp Business API)"
                            description="Connect a number that is not on WhatsApp yet (a new SIM). It is used only by this platform, not on a phone."
                            current={isLive && connection?.method === 'embedded'}
                            selected={launchOpen && launchMethod === 'embedded'}
                            locked={!planAllows}
                            lockText={lockText}
                            disabled={working}
                            onClick={() => chooseAutomatic('embedded')}
                        />
                    </Grid>
                )}
                <Grid item xs={12} md={automaticAvailable ? (coexAvailable ? 4 : 6) : 12}>
                    <MethodCard
                        icon={<SettingsIcon />}
                        color={theme.palette.text.secondary}
                        title="Manual configuration"
                        description="You already have your own Meta app: enter its IDs, token and webhook settings by hand."
                        current={manualCurrent}
                        selected={Boolean(manualVisible)}
                        disabled={working}
                        onClick={chooseManual}
                    />
                </Grid>
            </Grid>
            {!planAllows && automaticAvailable && (
                <Alert severity="warning" icon={<LockIcon />} sx={{ mt: 2, borderRadius: '12px' }}>
                    Automatic connection isn't included in your plan. Upgrade to connect WhatsApp by signing in to Meta, without copying credentials.
                </Alert>
            )}

            {/* ── Test message (US2) ── */}
            <Dialog open={testOpen} onClose={() => !working && setTestOpen(false)} fullWidth maxWidth="xs">
                <DialogTitle>Send a test message</DialogTitle>
                <DialogContent>
                    <DialogContentText sx={{ mb: 2 }}>
                        Sends Meta's sample "hello_world" message from your connected number. Use a WhatsApp number you
                        can check, with its country code.
                    </DialogContentText>
                    <TextField fullWidth autoFocus label="Send to" placeholder="+961 70 123 456" value={testTo}
                        onChange={(e) => setTestTo(e.target.value)} inputProps={{ inputMode: 'tel' }} />
                    {testResult && <Alert severity={testResult.severity} sx={{ mt: 2 }}>{testResult.text}</Alert>}
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setTestOpen(false)} disabled={working}>Close</Button>
                    <Button variant="contained" onClick={doSendTest} disabled={working || testTo.replace(/\D/g, '').length < 7}
                        startIcon={busy === 'testing' ? <CircularProgress size={14} /> : <SendIcon />}>
                        Send
                    </Button>
                </DialogActions>
            </Dialog>

            {/* ── Pre-launch dialog (also keeps FB.login inside a user click) ── */}
            <Dialog open={launchOpen} onClose={() => setLaunchOpen(false)} fullWidth maxWidth="sm">
                <DialogTitle sx={{ fontWeight: 700 }}>
                    {launchMethod === 'coexistence' ? 'Connect your WhatsApp Business app number' : 'Connect a new WhatsApp number'}
                </DialogTitle>
                <DialogContent>
                    {launchMethod === 'coexistence' ? (
                        <>
                            <DialogContentText sx={{ mb: 1 }}>
                                A Meta window opens. Sign in with Facebook, pick your business, enter your number, then confirm on your phone
                                (scan the QR code or accept the prompt in the WhatsApp Business app). Only new messages appear in the Inbox; past chats are not imported.
                            </DialogContentText>
                            <Alert severity="info" sx={{ borderRadius: '12px', mb: 1 }}>
                                <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>After connecting, these stop working on that number:</Typography>
                                <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                                    <li>Groups</li>
                                    <li>Broadcast lists</li>
                                    <li>Disappearing messages</li>
                                    <li>Windows and WearOS companion apps</li>
                                </Box>
                                <Typography variant="body2" sx={{ mt: 0.5 }}>
                                    Open the WhatsApp Business app on the phone at least every 14 days, or Meta disconnects the number.
                                </Typography>
                            </Alert>
                        </>
                    ) : (
                        <DialogContentText sx={{ mb: 1 }}>
                            A Meta window opens. Sign in with Facebook, create or pick your business and WhatsApp account, add the number,
                            and verify it with the code Meta sends by SMS or voice call. Keep the phone with that SIM nearby.
                        </DialogContentText>
                    )}
                    {isLive && (
                        <Alert severity="info" variant="outlined" sx={{ borderRadius: '12px', mt: 1 }}>
                            Your current connection stays active until the new one succeeds.
                        </Alert>
                    )}
                    {sdkState === 'error' && (
                        <Alert severity="error" sx={{ borderRadius: '12px', mt: 1 }}
                            action={<Button color="inherit" size="small" onClick={prepareSdk}>Retry</Button>}>
                            {sdkError}
                        </Alert>
                    )}
                </DialogContent>
                <DialogActions sx={{ px: 3, pb: 2 }}>
                    <Button onClick={() => setLaunchOpen(false)} sx={{ textTransform: 'none' }}>Cancel</Button>
                    <Button variant="contained" onClick={() => launchFlow(launchMethod)} disabled={sdkState !== 'ready'}
                        startIcon={sdkState === 'loading' ? <CircularProgress size={16} color="inherit" /> : null}
                        sx={{ textTransform: 'none', fontWeight: 600, borderRadius: '10px', bgcolor: '#1877F2', '&:hover': { bgcolor: '#166FE5' } }}>
                        {sdkState === 'loading' ? 'Loading Facebook…' : 'Continue with Facebook'}
                    </Button>
                </DialogActions>
            </Dialog>

            {/* ── Switch to manual ── */}
            <Dialog open={confirmManualOpen} onClose={() => setConfirmManualOpen(false)} maxWidth="xs" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Switch to manual configuration?</DialogTitle>
                <DialogContent>
                    <DialogContentText>
                        Switching to manual will disconnect the current automatic connection. Automatic sending stops until you
                        enter your own Meta app credentials. Message history is kept.
                    </DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setConfirmManualOpen(false)} sx={{ textTransform: 'none' }}>Cancel</Button>
                    <Button color="error" variant="contained" sx={{ textTransform: 'none' }}
                        onClick={() => { setConfirmManualOpen(false); doDisconnect({ toManual: true }); }}>
                        Disconnect and switch
                    </Button>
                </DialogActions>
            </Dialog>

            {/* ── Disconnect ── */}
            <Dialog open={confirmDisconnectOpen} onClose={() => setConfirmDisconnectOpen(false)} maxWidth="xs" fullWidth>
                <DialogTitle sx={{ fontWeight: 700 }}>Disconnect WhatsApp?</DialogTitle>
                <DialogContent>
                    <DialogContentText>
                        Automatic sending stops and the stored access is removed. Messages fall back to manual WhatsApp links.
                        Your message history is kept, and you can reconnect later.
                    </DialogContentText>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setConfirmDisconnectOpen(false)} sx={{ textTransform: 'none' }}>Cancel</Button>
                    <Button color="error" variant="contained" sx={{ textTransform: 'none' }}
                        onClick={() => { setConfirmDisconnectOpen(false); doDisconnect({ toManual: false }); }}>
                        Disconnect
                    </Button>
                </DialogActions>
            </Dialog>

            <StandardTemplatesDialog
                open={templatesOpen}
                onClose={() => setTemplatesOpen(false)}
                onSubmitted={(results) => { if (results.some(r => r.ok)) setHasStandardTemplates(true); }}
            />
            {/* Keeps the WhatsApp Manager link discoverable even without a warning. */}
            {automaticLive && !paymentWarn && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
                    Billing, number settings and template review are also visible in{' '}
                    <Link href={WA_MANAGER_URL} target="_blank" rel="noopener noreferrer">WhatsApp Manager</Link>.
                </Typography>
            )}
        </Paper>
    );
};

export default WhatsAppConnectCard;
