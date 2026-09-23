import React, { useEffect, useRef } from 'react';
import {
    Box, Stack, Typography, Button, Chip, IconButton, Tooltip, Paper, Collapse
} from '@mui/material';
import {
    ArrowBack as BackIcon, Done as SentIcon, DoneAll as DeliveredIcon, ErrorOutline as FailedIcon,
    SmartToy as AiIcon, Reply as ReplyIcon, AddReaction as ReactIcon, CheckCircle as ResolveIcon,
    PauseCircle as PauseIcon
} from '@mui/icons-material';
import InboxMedia from './InboxMedia';
import { REASON_META, describeWindow } from './inboxFormat';
import { formatStamp } from '../formatStamp';

const BUBBLE = {
    customer: { align: 'flex-start', bg: 'background.paper' },
    ai: { align: 'flex-end', bg: '#e3f2fd' },
    admin: { align: 'flex-end', bg: '#dcf8c6' },
    system: { align: 'flex-end', bg: '#f1f1f1' },
};

const StatusTick = ({ m }) => {
    if (m.direction !== 'out') return null;
    if (m.status === 'failed') return <Tooltip title={m.error_message || `Error ${m.error_code}`}><FailedIcon color="error" sx={{ fontSize: 14 }} /></Tooltip>;
    if (m.status === 'read') return <DeliveredIcon color="primary" sx={{ fontSize: 14 }} />;
    if (m.status === 'delivered') return <DeliveredIcon sx={{ fontSize: 14, color: 'text.secondary' }} />;
    return <SentIcon sx={{ fontSize: 14, color: 'text.secondary' }} />;
};

const FAILED_MEDIA_LABEL = { voice: '🎤 Voice note (not sent)', sticker: 'Sticker (not sent)' };

const Bubble = ({ m, byWamid, onReply, onReact, windowOpen }) => {
    const [showTranscript, setShowTranscript] = React.useState(false);
    const style = BUBBLE[m.sender] || BUBBLE.customer;
    const quoted = m.reply_to_wa_message_id ? byWamid[m.reply_to_wa_message_id] : null;
    const isSticker = m.msg_type === 'sticker';
    const canInteract = m.direction === 'in' && m.wa_message_id && windowOpen;
    const canReply = canInteract;
    const canReact = canInteract;
    const failedEmptyLabel = m.status === 'failed' && m.media_status === 'none' && !m.text
        ? FAILED_MEDIA_LABEL[m.msg_type]
        : null;
    return (
        <Box sx={{ display: 'flex', justifyContent: style.align, mb: 1, '&:hover .bubble-actions': { opacity: 1 } }}>
            <Box sx={{ maxWidth: '75%' }}>
                <Paper elevation={0} sx={{ p: isSticker ? 0 : 1.25, bgcolor: isSticker ? 'transparent' : style.bg, borderRadius: 2, border: isSticker ? 0 : 1, borderColor: 'divider' }}>
                    {m.sender !== 'customer' && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                            {m.sender === 'ai' ? <><AiIcon sx={{ fontSize: 14 }} /> AI</> : m.sender === 'admin' ? (m.sent_by || 'Admin') : 'Auto-reply'}
                        </Typography>
                    )}
                    {quoted && (
                        <Box sx={{ borderLeft: 3, borderColor: 'primary.main', pl: 1, mb: 0.5, opacity: 0.8 }}>
                            <Typography variant="caption" noWrap display="block">{quoted.text || quoted.msg_type}</Typography>
                        </Box>
                    )}
                    {m.media_status !== 'none' && <InboxMedia message={m} />}
                    {failedEmptyLabel && (
                        <Typography variant="body2" color="text.secondary" fontStyle="italic">{failedEmptyLabel}</Typography>
                    )}
                    {m.text && m.msg_type !== 'sticker' && (
                        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                            {m.msg_type === 'location'
                                ? <a href={`https://maps.google.com/?q=${encodeURIComponent(m.text.split(' ')[0])}`} target="_blank" rel="noreferrer">📍 {m.text}</a>
                                : m.text}
                        </Typography>
                    )}
                    {m.transcript && (
                        <>
                            <Button size="small" onClick={() => setShowTranscript(s => !s)} sx={{ p: 0, minWidth: 0, textTransform: 'none' }}>
                                {showTranscript ? 'Hide transcript' : 'Show transcript'}
                            </Button>
                            <Collapse in={showTranscript}><Typography variant="body2" color="text.secondary" dir="auto">{m.transcript}</Typography></Collapse>
                        </>
                    )}
                    <Stack direction="row" spacing={0.5} alignItems="center" justifyContent="flex-end">
                        <Typography variant="caption" color="text.secondary">{formatStamp(m.created_at)}</Typography>
                        <StatusTick m={m} />
                    </Stack>
                </Paper>
                <Stack direction="row" spacing={0.5} justifyContent={style.align}>
                    {m.reactions?.map(r => <Chip key={r.side} size="small" label={r.emoji} sx={{ mt: -1, height: 22 }} />)}
                    {(canReply || canReact) && (
                        <Box className="bubble-actions" sx={{ opacity: { xs: 1, md: 0 }, transition: 'opacity .15s' }}>
                            {canReply && (
                                <IconButton size="small" aria-label="Reply" onClick={() => onReply(m)}><ReplyIcon sx={{ fontSize: 16 }} /></IconButton>
                            )}
                            {canReact && (
                                <IconButton size="small" aria-label="React" onClick={(e) => onReact(m, e.currentTarget)}><ReactIcon sx={{ fontSize: 16 }} /></IconButton>
                            )}
                        </Box>
                    )}
                </Stack>
            </Box>
        </Box>
    );
};

const ChatThread = ({ conversation, messages, hasMore, onLoadOlder, onBack, onResolve, onPause, onReply, onReact, children }) => {
    const bottomRef = useRef(null);
    const lastId = messages.length ? messages[messages.length - 1].id : null;
    useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end' }); }, [lastId]);
    const byWamid = Object.fromEntries(messages.filter(m => m.wa_message_id).map(m => [m.wa_message_id, m]));
    const cust = conversation.customer;
    const windowOpen = describeWindow(conversation.window_expires_at).open;
    return (
        <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ p: 1.5, borderBottom: 1, borderColor: 'divider' }}>
                {onBack && <IconButton onClick={onBack}><BackIcon /></IconButton>}
                <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography fontWeight={800} noWrap>{cust?.name || conversation.contact_name || `+${conversation.wa_phone}`}</Typography>
                    <Typography variant="caption" color="text.secondary" noWrap display="block">
                        +{conversation.wa_phone}{cust ? ` · ${cust.plan || ''} · ${cust.status} · balance ${cust.balance}` : ' · not linked to a customer'}
                    </Typography>
                </Box>
                {conversation.needs_attention && conversation.attention_reason && (
                    <Chip size="small" color={REASON_META[conversation.attention_reason]?.color} label={REASON_META[conversation.attention_reason]?.label} />
                )}
                {!conversation.ai_paused && <Button size="small" startIcon={<PauseIcon />} onClick={onPause}>Pause AI</Button>}
                {(conversation.needs_attention || conversation.ai_paused) && (
                    <Button size="small" variant="contained" startIcon={<ResolveIcon />} onClick={onResolve}>Resolve</Button>
                )}
            </Stack>
            <Box sx={{ flex: 1, overflowY: 'auto', p: 2, bgcolor: '#efeae2' }}>
                {hasMore && <Box sx={{ textAlign: 'center', mb: 1 }}><Button size="small" onClick={onLoadOlder}>Load older</Button></Box>}
                {messages.map(m => <Bubble key={m.id} m={m} byWamid={byWamid} onReply={onReply} onReact={onReact} windowOpen={windowOpen} />)}
                <div ref={bottomRef} />
            </Box>
            {conversation.ai_paused && (
                <Typography variant="caption" sx={{ px: 2, py: 0.5, bgcolor: 'warning.light' }}>
                    AI is paused for this chat — press Resolve to hand it back to the AI.
                </Typography>
            )}
            {children}
        </Box>
    );
};

export default ChatThread;
