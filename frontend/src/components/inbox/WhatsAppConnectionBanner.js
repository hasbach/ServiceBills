import React, { useEffect, useState } from 'react';
import { Alert } from '@mui/material';
import { useAppContext } from '../../context/AppContext';

// Amber warning above the inbox while an automatic WhatsApp connection is
// broken (US4): sends are refused until the admin reconnects in Settings.
const WhatsAppConnectionBanner = () => {
    const { apiService } = useAppContext();
    const [connection, setConnection] = useState(null);
    const [dismissed, setDismissed] = useState(false);

    useEffect(() => {
        let alive = true;
        apiService.getWaConnectStatus()
            .then((res) => { if (alive) setConnection(res.data?.connection || null); })
            .catch(() => { /* not configured / not admin: no banner */ });
        return () => { alive = false; };
    }, [apiService]);

    if (dismissed || !connection || connection.status !== 'needs_attention') return null;
    return (
        <Alert severity="warning" onClose={() => setDismissed(true)} sx={{ mx: 2, mt: 2 }}>
            WhatsApp needs attention{connection.status_reason ? `: ${connection.status_reason}` : '.'} Messages
            can't be sent until you reconnect in Settings &gt; WhatsApp.
        </Alert>
    );
};

export default WhatsAppConnectionBanner;
