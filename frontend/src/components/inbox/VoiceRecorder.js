import React, { useEffect, useRef, useState } from 'react';
import { IconButton, Stack, Typography, Tooltip } from '@mui/material';
import { Mic as MicIcon, Stop as StopIcon, Send as SendIcon, Delete as DeleteIcon } from '@mui/icons-material';

const MAX_SECONDS = 300;
const pickMime = () => {
    if (typeof MediaRecorder === 'undefined') return null;
    return ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find(t => MediaRecorder.isTypeSupported(t)) || '';
};

const VoiceRecorder = ({ disabled, onSend, onError }) => {
    const [state, setState] = useState('idle'); // idle | recording | review
    const [seconds, setSeconds] = useState(0);
    const [blob, setBlob] = useState(null);
    const [previewUrl, setPreviewUrl] = useState(null);
    const [sendingVoice, setSendingVoice] = useState(false);
    const mountedRef = useRef(true);
    const recRef = useRef(null);
    const timerRef = useRef(null);
    const streamRef = useRef(null);
    const previewUrlRef = useRef(null);

    const stop = () => { clearInterval(timerRef.current); recRef.current?.state === 'recording' && recRef.current.stop(); };

    useEffect(() => { previewUrlRef.current = previewUrl; }, [previewUrl]);
    useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);

    useEffect(() => () => {
        clearInterval(timerRef.current);
        const rec = recRef.current;
        if (rec) {
            rec.ondataavailable = null;
            rec.onstop = null;
            if (rec.state === 'recording') rec.stop();
        }
        streamRef.current?.getTracks().forEach(t => t.stop());
        streamRef.current = null;
        if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    }, []);
    useEffect(() => { if (state === 'recording' && seconds >= MAX_SECONDS) stop(); }, [seconds, state]);

    const start = async () => {
        const mime = pickMime();
        if (mime === null || !navigator.mediaDevices?.getUserMedia) { onError('This browser cannot record audio.'); return; }
        let stream;
        try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
        catch (e) { onError('Microphone permission was denied.'); return; }
        streamRef.current = stream;
        const chunks = [];
        const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        rec.ondataavailable = e => e.data.size && chunks.push(e.data);
        rec.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            streamRef.current = null;
            const b = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
            setBlob(b); setPreviewUrl(URL.createObjectURL(b)); setState('review');
        };
        recRef.current = rec;
        rec.start();
        setSeconds(0); setState('recording');
        timerRef.current = setInterval(() => setSeconds(s => s + 1), 1000);
    };
    const discard = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); setBlob(null); setPreviewUrl(null); setState('idle'); };
    const send = async () => {
        if (sendingVoice || !blob) return;
        const ext = (blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm');
        setSendingVoice(true);
        try {
            await onSend(new File([blob], `voice.${ext}`, { type: blob.type }));
        } finally {
            if (mountedRef.current) { setSendingVoice(false); discard(); }
        }
    };

    if (state === 'recording') return (
        <Stack direction="row" alignItems="center" spacing={1}>
            <Typography color="error" variant="body2">● {Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</Typography>
            <IconButton color="error" onClick={stop}><StopIcon /></IconButton>
        </Stack>
    );
    if (state === 'review') return (
        <Stack direction="row" alignItems="center" spacing={1}>
            <audio controls src={previewUrl} style={{ height: 36, maxWidth: 220 }} />
            <IconButton onClick={discard} disabled={sendingVoice}><DeleteIcon /></IconButton>
            <IconButton color="primary" onClick={send} disabled={sendingVoice}><SendIcon /></IconButton>
        </Stack>
    );
    return (
        <Tooltip title={disabled ? 'Voice notes unavailable on this server' : 'Record voice note'}>
            <span><IconButton disabled={disabled} onClick={start}><MicIcon /></IconButton></span>
        </Tooltip>
    );
};

export default VoiceRecorder;
