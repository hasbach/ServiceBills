import React, { useCallback, useEffect, useState } from 'react';
import { Button, Menu, MenuItem, Tooltip, Typography } from '@mui/material';
import { NotificationsActive as OnIcon, NotificationsOff as OffIcon, NotificationsNone as NoneIcon } from '@mui/icons-material';
import { useAppContext } from '../../context/AppContext';
import * as swReg from '../../serviceWorkerRegistration';

const TOPIC = 'whatsapp_inbox';
const SW_READY_TIMEOUT_MS = 3000;
const isIos = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
const isStandalone = () => window.matchMedia?.('(display-mode: standalone)').matches || window.navigator.standalone === true;

const InboxNotificationsControl = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [state, setState] = useState('loading'); // loading|unsupported|ios_install|not_configured|blocked|off|on
    const [anchor, setAnchor] = useState(null);
    const toast = (message, severity = 'info') => setSnackbar({ open: true, message, severity });

    // navigator.serviceWorker.ready never resolves when no service worker is
    // registered (e.g. plain `npm start`), so race it against a short timeout
    // rather than hang the control on 'loading' forever.
    const getRegistration = () => Promise.race([
        navigator.serviceWorker.ready,
        new Promise((_, reject) => setTimeout(() => reject(new Error('sw_ready_timeout')), SW_READY_TIMEOUT_MS)),
    ]);

    const currentSub = async () => {
        const reg = await getRegistration();
        return reg.pushManager.getSubscription();
    };

    const refresh = useCallback(async () => {
        if (isIos() && !isStandalone()) return setState('ios_install');
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return setState('unsupported');
        if (Notification.permission === 'denied') return setState('blocked');
        try {
            const { data } = await apiService.fetchVapidPublicKey();
            if (!data.public_key) return setState('not_configured');
            let sub;
            try {
                sub = await currentSub();
            } catch (e) {
                return setState('unsupported');
            }
            if (!sub) return setState('off');
            const topics = await apiService.fetchPushTopics(sub.endpoint);
            setState(topics.data.subscribed && topics.data.topics.includes(TOPIC) ? 'on' : 'off');
        } catch (e) { setState('off'); }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiService]);

    useEffect(() => { refresh(); }, [refresh]);

    const enable = async () => {
        try {
            const { data } = await apiService.fetchVapidPublicKey();
            if (!data.public_key) { setState('not_configured'); return; }
            let sub;
            try {
                sub = await currentSub();
            } catch (e) {
                setState('unsupported');
                return;
            }
            if (!sub) sub = await swReg.subscribeUserToPush(data.public_key);
            await apiService.pushSubscribe(sub.toJSON ? sub.toJSON() : sub);
            const { data: t } = await apiService.fetchPushTopics(sub.endpoint);
            const topics = Array.from(new Set([...(t.topics || []), TOPIC]));
            await apiService.setPushTopics(sub.endpoint, topics);
            toast('Inbox notifications enabled on this device', 'success');
        } catch (e) {
            // If the user denied the permission prompt, refresh() below will
            // pick that up via Notification.permission and show 'blocked' --
            // avoid also popping an error toast in that case.
            const denied = typeof Notification !== 'undefined' && Notification.permission === 'denied';
            if (!denied) toast(`Could not enable notifications: ${e.response?.data?.msg || e.message}`, 'error');
        }
        await refresh();
    };

    const disable = async () => {
        setAnchor(null);
        try {
            const sub = await currentSub();
            if (!sub) { await refresh(); return; }
            const { data: t } = await apiService.fetchPushTopics(sub.endpoint);
            await apiService.setPushTopics(sub.endpoint, (t.topics || []).filter(x => x !== TOPIC));
        } catch (e) {
            toast(e.response?.data?.msg || 'Could not update notification settings', 'error');
        }
        await refresh();
    };

    const test = async () => {
        setAnchor(null);
        try {
            const sub = await currentSub();
            if (!sub) { await refresh(); return; }
            await apiService.sendTestPush(sub.endpoint);
            toast('Test notification sent', 'success');
        } catch (e) {
            toast(e.response?.data?.msg || 'Test failed', 'error');
        }
    };

    if (state === 'loading') return null;
    if (state === 'ios_install') return <Typography variant="caption" color="text.secondary">On iPhone: Share → Add to Home Screen, then open the app to enable notifications.</Typography>;
    if (state === 'unsupported') return <Typography variant="caption" color="text.secondary">This browser doesn't support notifications.</Typography>;
    if (state === 'not_configured') return <Typography variant="caption" color="text.secondary">Notifications are not configured on the server.</Typography>;
    if (state === 'blocked') return <Tooltip title="Allow notifications for this site in your browser settings"><span><Button size="small" startIcon={<OffIcon />} disabled>Notifications blocked</Button></span></Tooltip>;
    if (state === 'off') return <Button size="small" variant="outlined" startIcon={<NoneIcon />} onClick={enable}>Enable notifications</Button>;
    return (
        <>
            <Button size="small" color="success" startIcon={<OnIcon />} onClick={e => setAnchor(e.currentTarget)}>Notifications on</Button>
            <Menu open={!!anchor} anchorEl={anchor} onClose={() => setAnchor(null)}>
                <MenuItem onClick={test}>Send test notification</MenuItem>
                <MenuItem onClick={disable}>Turn off on this device</MenuItem>
            </Menu>
        </>
    );
};

export default InboxNotificationsControl;
