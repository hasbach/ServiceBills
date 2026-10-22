import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Alert, Box, Button, CircularProgress, Paper, Stack, TextField, Typography } from '@mui/material';
import { SmartToy as SmartToyIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext';
import { consentParams, describeRole } from './oauthConsent';

const NAME_MAX = 60;
const shortName = (name) => {
    const n = String(name || '');
    return n.length > NAME_MAX ? `${n.slice(0, NAME_MAX)}…` : n;
};

// "Claude wants read-only access to <business> as <user>". Reached from
// GET /oauth/authorize (mcp_oauth.py) with the OAuth request in the query.
// Signs the user in first if needed, then approves or denies and sends the
// browser back to the AI app with a one-time code.
export default function OAuthConsentView() {
    const { isAuthenticated, login, logout } = useAppContext();
    const [params] = useState(() => consentParams(window.location.search));
    const [info, setInfo] = useState(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [creds, setCreds] = useState({ username: '', password: '' });

    useEffect(() => {
        // A different person may have just signed in: drop the previous
        // identity's data and error before fetching, and ignore late replies.
        setInfo(null);
        setError('');
        if (!isAuthenticated) return undefined;
        let cancelled = false;
        const token = localStorage.getItem('token');
        axios.get('/api/mcp/oauth/consent-info', { params, headers: { Authorization: `Bearer ${token}` } })
            .then(res => { if (!cancelled) setInfo(res.data); })
            .catch(err => {
                if (!cancelled) setError(err.response?.data?.error || err.response?.data?.msg || 'This connection request is not valid.');
            });
        return () => { cancelled = true; };
    }, [isAuthenticated, params]);

    const signIn = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError('');
        try {
            await login(creds);
        } catch (err) {
            setError(err.response?.data?.msg || 'Sign-in failed.');
        } finally {
            setBusy(false);
        }
    };

    const decide = async (decision) => {
        setBusy(true);
        setError('');
        try {
            const token = localStorage.getItem('token');
            const res = await axios.post('/api/mcp/oauth/approve', { ...params, decision },
                { headers: { Authorization: `Bearer ${token}` } });
            window.location.assign(res.data.redirect_url);
        } catch (err) {
            setError(err.response?.data?.error || 'Could not complete the connection.');
            setBusy(false);
        }
    };

    return (
        <Box sx={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', p: 2, bgcolor: 'background.default' }}>
            <Paper variant="outlined" sx={{ p: 4, maxWidth: 440, width: '100%', borderRadius: '16px' }}>
                <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
                    <SmartToyIcon color="primary" />
                    <Typography variant="h6">Connect an AI app to ServiceBills</Typography>
                </Stack>
                {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
                {!isAuthenticated && (
                    <form onSubmit={signIn}>
                        <Typography variant="body2" sx={{ mb: 2 }}>Sign in with your ServiceBills account to continue.</Typography>
                        <TextField fullWidth label="Username" sx={{ mb: 2 }} value={creds.username}
                            onChange={e => setCreds(c => ({ ...c, username: e.target.value }))} />
                        <TextField fullWidth type="password" label="Password" sx={{ mb: 2 }} value={creds.password}
                            onChange={e => setCreds(c => ({ ...c, password: e.target.value }))} />
                        <Button fullWidth type="submit" variant="contained" disabled={busy}>Sign in</Button>
                    </form>
                )}
                {isAuthenticated && !info && error && (
                    <Button size="small" onClick={logout}>Use a different account</Button>
                )}
                {isAuthenticated && !info && !error && <Box sx={{ textAlign: 'center', py: 3 }}><CircularProgress /></Box>}
                {isAuthenticated && info && (
                    <>
                        <Typography sx={{ mb: 1.5, fontSize: '1.05rem' }}>
                            Will send you back to: <b>{info.redirect_host}</b>
                        </Typography>
                        {!info.verified && (
                            <Alert severity="warning" sx={{ mb: 2 }}>
                                Unverified app — only continue if you added this connector yourself in Claude or ChatGPT.
                            </Alert>
                        )}
                        <Typography sx={{ mb: 1, overflowWrap: 'anywhere' }}>
                            <b>{shortName(info.client_name)}</b> wants <b>read-only</b> access to <b>{info.tenant_name}</b> as{' '}
                            <b>{info.username}</b> ({describeRole(info.role)}).
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                            It will be able to look up customers, payments, reports, tickets and network status that
                            your role can see. It cannot change anything, take payments or send messages.
                        </Typography>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
                            You can disconnect it any time in Settings → AI Connector.
                        </Typography>
                        <Stack direction="row" spacing={1.5}>
                            <Button fullWidth variant="contained" disabled={busy} onClick={() => decide('allow')}>Allow</Button>
                            <Button fullWidth variant="outlined" disabled={busy} onClick={() => decide('deny')}>Deny</Button>
                        </Stack>
                        <Button size="small" sx={{ mt: 2 }} onClick={logout}>Not {info.username}? Sign out</Button>
                    </>
                )}
            </Paper>
        </Box>
    );
}
