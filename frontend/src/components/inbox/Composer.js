import React, { useEffect, useRef, useState } from 'react';
import {
    Box, Stack, TextField, IconButton, Popover, Chip, Typography, Tooltip, MenuItem, Button, Select, FormControl, InputLabel
} from '@mui/material';
import { Send as SendIcon, EmojiEmotions as EmojiIcon, Image as StickerIcon, Close as CloseIcon } from '@mui/icons-material';
import EmojiPicker from 'emoji-picker-react';
import { useAppContext } from '../../context/AppContext';
import VoiceRecorder from './VoiceRecorder';
import { describeWindow, templateParamCount } from './inboxFormat';

const QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏'];

const TemplateSender = ({ conversationId, onSent, notify }) => {
    const { apiService } = useAppContext();
    const [templates, setTemplates] = useState([]);
    const [name, setName] = useState('');
    const [params, setParams] = useState([]);
    useEffect(() => {
        apiService.fetchWhatsAppTemplates()
            .then(r => setTemplates((r.data.templates || []).filter(t => (t.status || '').toUpperCase() === 'APPROVED')))
            .catch(() => {});
    }, [apiService]);
    const selected = templates.find(t => `${t.name}|${t.language}` === name);
    const count = templateParamCount(selected);
    useEffect(() => setParams(Array(count).fill('')), [name, count]);
    const send = async () => {
        try {
            await apiService.sendInboxMessage(conversationId, { type: 'template', template_name: selected.name, body_params: params });
            setName(''); onSent();
        } catch (e) { notify(e.response?.data?.msg || 'Template send failed'); }
    };
    return (
        <Stack spacing={1}>
            <FormControl size="small" fullWidth>
                <InputLabel>Approved template</InputLabel>
                <Select label="Approved template" value={name} onChange={e => setName(e.target.value)}>
                    {templates.map(t => <MenuItem key={`${t.name}-${t.language}`} value={`${t.name}|${t.language}`}>{t.name} ({t.language})</MenuItem>)}
                </Select>
            </FormControl>
            {params.map((p, i) => (
                <TextField key={i} size="small" label={`{{${i + 1}}}`} value={p}
                    onChange={e => setParams(ps => ps.map((x, j) => (j === i ? e.target.value : x)))} />
            ))}
            <Button variant="contained" disabled={!name || params.some(p => !p.trim())} onClick={send}>Send template</Button>
        </Stack>
    );
};

const Composer = ({ conversation, replyTo, clearReply, reactTarget, clearReactTarget, onSent }) => {
    const { apiService, setSnackbar } = useAppContext();
    const [text, setText] = useState('');
    const [sending, setSending] = useState(false);
    const [emojiAnchor, setEmojiAnchor] = useState(null);
    const [fullReactPicker, setFullReactPicker] = useState(false);
    const [voiceAvailable, setVoiceAvailable] = useState(true);
    const [, forceTick] = useState(0);
    const fileRef = useRef(null);
    const notify = (message) => setSnackbar({ open: true, message, severity: 'error' });

    useEffect(() => { apiService.fetchInboxSummary().then(r => setVoiceAvailable(!!r.data.voice_available)).catch(() => {}); }, [apiService]);
    useEffect(() => { const i = setInterval(() => forceTick(t => t + 1), 30000); return () => clearInterval(i); }, []);

    const windowInfo = describeWindow(conversation.window_expires_at);
    const run = async (fn) => {
        setSending(true);
        try { await fn(); await onSent(); }
        catch (e) { notify(e.response?.data?.msg || 'Send failed'); if (e.response?.data?.error === 'window_closed') await onSent(); }
        finally { setSending(false); }
    };
    const sendText = () => text.trim() && !sending && run(async () => {
        await apiService.sendInboxMessage(conversation.id, { type: 'text', text, reply_to: replyTo?.wa_message_id });
        setText(''); clearReply();
    });
    const sendReaction = (emoji) => {
        const target = reactTarget?.message;
        clearReactTarget(); setFullReactPicker(false);
        if (target && !sending) run(() => apiService.sendInboxMessage(conversation.id, { type: 'reaction', target: target.wa_message_id, emoji }));
    };
    const sendSticker = (file) => file && !sending && run(() => apiService.sendInboxFile(conversation.id, 'sticker', file));
    const sendVoice = (file) => run(() => apiService.sendInboxFile(conversation.id, 'voice', file));

    const reactionPopover = (
        <Popover open={!!reactTarget} anchorEl={reactTarget?.anchorEl} onClose={() => { clearReactTarget(); setFullReactPicker(false); }}
            anchorOrigin={{ vertical: 'top', horizontal: 'center' }} transformOrigin={{ vertical: 'bottom', horizontal: 'center' }}>
            {fullReactPicker
                ? <EmojiPicker onEmojiClick={(d) => sendReaction(d.emoji)} lazyLoadEmojis />
                : (
                    <Stack direction="row" sx={{ p: 0.5 }}>
                        {QUICK_REACTIONS.map(e => <IconButton key={e} onClick={() => sendReaction(e)} sx={{ fontSize: 22 }}>{e}</IconButton>)}
                        <IconButton onClick={() => setFullReactPicker(true)}>＋</IconButton>
                    </Stack>
                )}
        </Popover>
    );

    if (!windowInfo.open) return (
        <Box sx={{ p: 2, borderTop: 1, borderColor: 'divider' }}>
            <Typography variant="body2" color="warning.main" sx={{ mb: 1 }}>{windowInfo.label}</Typography>
            <TemplateSender conversationId={conversation.id} onSent={onSent} notify={notify} />
        </Box>
    );

    return (
        <Box sx={{ p: 1.5, borderTop: 1, borderColor: 'divider' }}>
            {reactionPopover}
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
                {replyTo ? (
                    <Chip size="small" onDelete={clearReply} deleteIcon={<CloseIcon />}
                        label={`Replying to: ${(replyTo.text || replyTo.msg_type).slice(0, 40)}`} />
                ) : <span />}
                <Typography variant="caption" color="text.secondary">{windowInfo.label}</Typography>
            </Stack>
            <Stack direction="row" spacing={0.5} alignItems="flex-end">
                <IconButton onClick={e => setEmojiAnchor(e.currentTarget)}><EmojiIcon /></IconButton>
                <Popover open={!!emojiAnchor} anchorEl={emojiAnchor} onClose={() => setEmojiAnchor(null)}
                    anchorOrigin={{ vertical: 'top', horizontal: 'left' }} transformOrigin={{ vertical: 'bottom', horizontal: 'left' }}>
                    <EmojiPicker onEmojiClick={(d) => setText(t => t + d.emoji)} lazyLoadEmojis />
                </Popover>
                <Tooltip title="Send sticker (.webp, or PNG/JPG converted to 512×512)">
                    <IconButton onClick={() => fileRef.current?.click()} disabled={sending}><StickerIcon /></IconButton>
                </Tooltip>
                <input ref={fileRef} type="file" accept="image/webp,image/png,image/jpeg" hidden
                    onChange={e => { sendSticker(e.target.files?.[0]); e.target.value = ''; }} />
                <TextField fullWidth multiline maxRows={5} size="small" placeholder="Type a message" value={text} dir="auto"
                    onChange={e => setText(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); } }} />
                {text.trim()
                    ? <IconButton color="primary" disabled={sending} onClick={sendText}><SendIcon /></IconButton>
                    : <VoiceRecorder disabled={!voiceAvailable || sending} onSend={sendVoice} onError={notify} />}
            </Stack>
        </Box>
    );
};

export default Composer;
