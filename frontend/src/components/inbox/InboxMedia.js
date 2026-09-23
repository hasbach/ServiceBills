import React, { useEffect, useState } from 'react';
import { Box, CircularProgress, Link, Typography, Dialog } from '@mui/material';
import { useAppContext } from '../../context/AppContext';

// Media needs the JWT header, so it's fetched as a blob and shown via an
// object URL (an <img src> straight at the API can't send Authorization).
const useMediaUrl = (messageId, variant, enabled) => {
    const { apiService } = useAppContext();
    const [url, setUrl] = useState(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        if (!enabled) return undefined;
        let objectUrl = null;
        let cancelled = false;
        apiService.fetchInboxMedia(messageId, variant)
            .then(res => { if (!cancelled) { objectUrl = URL.createObjectURL(res.data); setUrl(objectUrl); } })
            .catch(() => { if (!cancelled) setFailed(true); });
        return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [apiService, messageId, variant, enabled]);
    return { url, failed };
};

const InboxMedia = ({ message }) => {
    const [zoom, setZoom] = useState(false);
    const stored = message.media_status === 'stored';
    const variant = message.msg_type === 'audio' && message.has_playback ? 'playback' : 'original';
    const { url, failed } = useMediaUrl(message.id, variant, stored);

    if (message.media_status === 'pending') return <Typography variant="caption" color="text.secondary">Downloading media…</Typography>;
    if (!stored || failed) return <Typography variant="caption" color="text.secondary">Media unavailable</Typography>;
    if (!url) return <CircularProgress size={18} />;

    switch (message.msg_type) {
        case 'audio':
            return <audio controls src={url} style={{ maxWidth: 260 }} />;
        case 'image':
            return (
                <>
                    <Box component="img" src={url} alt="" onClick={() => setZoom(true)}
                        sx={{ maxWidth: 240, maxHeight: 240, borderRadius: 2, cursor: 'zoom-in', display: 'block' }} />
                    <Dialog open={zoom} onClose={() => setZoom(false)} maxWidth="lg">
                        <Box component="img" src={url} alt="" sx={{ maxWidth: '90vw', maxHeight: '90vh' }} />
                    </Dialog>
                </>
            );
        case 'sticker':
            return <Box component="img" src={url} alt="sticker" sx={{ width: 128, height: 128, display: 'block' }} />;
        case 'video':
            return <video controls src={url} style={{ maxWidth: 260, borderRadius: 8 }} />;
        default:
            return <Link href={url} download>Download {message.text || 'file'}</Link>;
    }
};

export default InboxMedia;
