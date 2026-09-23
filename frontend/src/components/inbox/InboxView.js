import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Paper, Typography, useMediaQuery, useTheme, Stack } from '@mui/material';
import { useAppContext } from '../../context/AppContext';
import ConversationList from './ConversationList';
import ChatThread from './ChatThread';
import { attachReactions } from './inboxFormat';

const LIST_POLL_MS = 20000;
const THREAD_POLL_MS = 5000;

const InboxView = ({ openConversationId = null, renderComposer = null, headerExtra = null }) => {
    const { apiService, setSnackbar } = useAppContext();
    const theme = useTheme();
    const isMobile = useMediaQuery(theme.breakpoints.down('md'));
    const [filter, setFilter] = useState(openConversationId ? 'all' : 'attention');
    const [search, setSearch] = useState('');
    const [conversations, setConversations] = useState([]);
    const [selectedId, setSelectedId] = useState(openConversationId);
    const [thread, setThread] = useState(null); // { conversation, messages, has_more }
    const [replyTo, setReplyTo] = useState(null);
    const [reactTarget, setReactTarget] = useState(null); // { message, anchorEl }
    const selectedRef = useRef(selectedId);
    selectedRef.current = selectedId;

    useEffect(() => { if (openConversationId) { setSelectedId(openConversationId); setFilter('all'); } }, [openConversationId]);

    const loadList = useCallback(async () => {
        try {
            const res = await apiService.fetchInboxConversations({ filter, q: search || undefined });
            // A pre-existing service-worker quirk can make a failed fetch resolve
            // with a string body instead of rejecting -- never let that crash the UI.
            setConversations(Array.isArray(res.data?.conversations) ? res.data.conversations : []);
        } catch (e) { /* polling: stay quiet */ }
    }, [apiService, filter, search]);

    const loadThread = useCallback(async (id, { markRead = false } = {}) => {
        if (!id) return;
        try {
            const res = await apiService.fetchInboxMessages(id);
            if (selectedRef.current !== id) return;
            if (!res.data || typeof res.data !== 'object' || !Array.isArray(res.data.messages)) return;
            setThread(res.data);
            if (markRead && res.data.conversation?.unread_count > 0) {
                await apiService.markInboxRead(id);
                loadList();
            }
        } catch (e) {
            if (e.response?.status === 404) { setSelectedId(null); setThread(null); }
        }
    }, [apiService, loadList]);

    useEffect(() => {
        const t = setTimeout(loadList, search ? 300 : 0); // debounce typing
        const i = setInterval(loadList, LIST_POLL_MS);
        return () => { clearTimeout(t); clearInterval(i); };
    }, [loadList, search]);

    useEffect(() => {
        setThread(null); setReplyTo(null);
        if (!selectedId) return undefined;
        loadThread(selectedId, { markRead: true });
        const i = setInterval(() => loadThread(selectedId, { markRead: true }), THREAD_POLL_MS);
        return () => clearInterval(i);
    }, [selectedId, loadThread]);

    const act = async (fn, okMsg) => {
        try {
            await fn();
            if (okMsg) setSnackbar({ open: true, message: okMsg, severity: 'success' });
            await Promise.all([loadThread(selectedId), loadList()]);
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Action failed', severity: 'error' });
        }
    };

    const refresh = useCallback(() => Promise.all([loadThread(selectedId), loadList()]), [loadThread, loadList, selectedId]);

    const listPane = (
        <ConversationList conversations={conversations} selectedId={selectedId} onSelect={setSelectedId}
            filter={filter} onFilterChange={setFilter} search={search} onSearchChange={setSearch} />
    );
    const threadPane = thread ? (
        <ChatThread
            conversation={thread.conversation}
            messages={attachReactions(thread.messages)}
            hasMore={thread.has_more}
            onLoadOlder={async () => {
                const oldest = thread.messages[0]?.id;
                const res = await apiService.fetchInboxMessages(selectedId, oldest);
                if (!res.data || !Array.isArray(res.data.messages)) return;
                setThread(t => ({ ...t, messages: [...res.data.messages, ...t.messages], has_more: res.data.has_more }));
            }}
            onBack={isMobile ? () => setSelectedId(null) : null}
            onResolve={() => act(() => apiService.resolveInboxConversation(selectedId), 'Handed back to the AI')}
            onPause={() => act(() => apiService.pauseInboxConversation(selectedId), 'AI paused for this chat')}
            onReply={setReplyTo}
            onReact={(message, anchorEl) => setReactTarget({ message, anchorEl })}
        >
            {renderComposer && renderComposer({
                conversation: thread.conversation, replyTo, clearReply: () => setReplyTo(null),
                reactTarget, clearReactTarget: () => setReactTarget(null), onSent: refresh,
            })}
        </ChatThread>
    ) : (
        <Box sx={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Typography color="text.secondary">{selectedId ? 'Loading…' : 'Select a conversation'}</Typography>
        </Box>
    );

    return (
        <Box>
            {headerExtra && <Stack direction="row" justifyContent="flex-end" sx={{ px: 2, pt: 2 }}>{headerExtra}</Stack>}
            <Paper elevation={0} sx={{ m: 2, border: 1, borderColor: 'divider', borderRadius: 3, overflow: 'hidden', height: { xs: 'calc(100vh - 220px)', md: 640 } }}>
                {isMobile ? (selectedId ? threadPane : listPane) : (
                    <Box sx={{ display: 'grid', gridTemplateColumns: '340px 1fr', height: '100%' }}>
                        <Box sx={{ borderRight: 1, borderColor: 'divider', minHeight: 0 }}>{listPane}</Box>
                        <Box sx={{ minHeight: 0 }}>{threadPane}</Box>
                    </Box>
                )}
            </Paper>
        </Box>
    );
};

export default InboxView;
