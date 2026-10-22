import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Alert, Box, Button, CircularProgress, IconButton, List, ListItem, ListItemText, Paper, Tooltip, Typography } from '@mui/material';
import { ContentCopy as ContentCopyIcon } from '@mui/icons-material';

const auth = () => ({ headers: { Authorization: `Bearer ${localStorage.getItem('token')}` } });

// The MCP connector URL plus every AI app connected to this business
// (admins see everyone's; see /api/mcp/grants in mcp_oauth.py).
export default function AIConnectorCard() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [copied, setCopied] = useState(false);

    const copiedTimer = useRef(null);
    useEffect(() => () => clearTimeout(copiedTimer.current), []);

    // keepError: reload after a failed action without wiping that action's message.
    const load = useCallback((keepError = false) => {
        axios.get('/api/mcp/grants', auth())
            .then(res => { setData(res.data); if (!keepError) setError(''); })
            .catch(err => setError(err.response?.data?.msg || 'Could not load AI connections.'));
    }, []);
    useEffect(() => { load(); }, [load]);

    const disconnect = async (g) => {
        if (!window.confirm(`Disconnect ${g.client_name || 'this AI app'} for ${g.username}?`)) return;
        let failed = false;
        try {
            await axios.delete(`/api/mcp/grants/${g.id}`, auth());
        } catch (err) {
            failed = true;
            setError(err.response?.data?.msg || 'Could not disconnect.');
        }
        // Reload either way: a grant revoked elsewhere should disappear.
        load(failed);
    };

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(data.mcp_url);
            setCopied(true);
            clearTimeout(copiedTimer.current);
            copiedTimer.current = setTimeout(() => setCopied(false), 2000);
        } catch (e) { setCopied(false); }
    };

    if (!data) {
        return error
            ? <Alert severity="error">{error}</Alert>
            : <Box sx={{ textAlign: 'center', py: 3 }}><CircularProgress size={24} /></Box>;
    }
    return (
        <Paper variant="outlined" sx={{ p: 3, borderRadius: '12px' }}>
            <Typography variant="h6" sx={{ mb: 1 }}>AI Connector</Typography>
            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
            {!data.enabled && (
                <Alert severity="info" sx={{ mb: 2 }}>The AI connector is not part of your plan. Contact ServiceBills to enable it.</Alert>
            )}
            <Typography variant="body2" sx={{ mb: 1 }}>
                Let Claude, ChatGPT or another AI app answer questions about your business (read-only).
                Add this URL as a custom connector:
            </Typography>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2, p: 1.5, bgcolor: 'action.hover', borderRadius: '8px' }}>
                <Typography data-testid="mcp-url" sx={{ fontFamily: 'monospace', wordBreak: 'break-all', flex: 1 }}>{data.mcp_url}</Typography>
                <Tooltip title={copied ? 'Copied' : 'Copy'}><IconButton size="small" aria-label="Copy connector URL" onClick={copy}><ContentCopyIcon fontSize="small" /></IconButton></Tooltip>
            </Box>
            <Typography variant="body2" color="text.secondary" component="div" sx={{ mb: 2 }}>
                <b>Claude:</b> Settings → Connectors → Add custom connector → paste the URL → Connect.<br />
                <b>ChatGPT:</b> Settings → Apps &amp; Connectors → Create (developer mode) → paste the URL → OAuth.<br />
                Each person signs in with their own ServiceBills account and sees only what their role allows.
            </Typography>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{data.is_admin ? 'Connected AI apps' : 'Your connected AI apps'}</Typography>
            {data.grants.length === 0 ? (
                <Typography variant="body2" color="text.secondary">No AI apps connected yet.</Typography>
            ) : (
                <List dense>
                    {data.grants.map(g => (
                        <ListItem key={g.id} divider secondaryAction={<Button size="small" color="error" onClick={() => disconnect(g)}>Disconnect</Button>}>
                            <ListItemText
                                primary={`${g.client_name || 'AI app'}: ${g.username} (${g.role})`}
                                secondary={`Connected ${(g.created_at || '').slice(0, 10)}${g.last_used_at ? ` · last used ${g.last_used_at.slice(0, 16).replace('T', ' ')} UTC` : ' · not used yet'}`} />
                        </ListItem>
                    ))}
                </List>
            )}
        </Paper>
    );
}
