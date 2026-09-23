import React from 'react';
import {
    Box, List, ListItemButton, ListItemText, Typography, Chip, Badge, TextField,
    ToggleButtonGroup, ToggleButton, Stack, InputAdornment
} from '@mui/material';
import { Search as SearchIcon } from '@mui/icons-material';
import { REASON_META } from './inboxFormat';
import { describeAge } from '../NetworkTreeView';

const ConversationList = ({ conversations, selectedId, onSelect, filter, onFilterChange, search, onSearchChange }) => (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <Stack spacing={1.5} sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
            <ToggleButtonGroup size="small" exclusive fullWidth value={filter} onChange={(e, v) => v && onFilterChange(v)}>
                <ToggleButton value="attention">Needs attention</ToggleButton>
                <ToggleButton value="unread">Unread</ToggleButton>
                <ToggleButton value="all">All</ToggleButton>
            </ToggleButtonGroup>
            <TextField size="small" placeholder="Search name or phone" value={search}
                onChange={e => onSearchChange(e.target.value)}
                InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> }} />
        </Stack>
        <List sx={{ flex: 1, overflowY: 'auto', py: 0 }}>
            {conversations.length === 0 && (
                <Typography sx={{ p: 3 }} color="text.secondary" align="center">
                    {filter === 'attention' ? 'Nothing needs attention 🎉' : 'No conversations'}
                </Typography>
            )}
            {conversations.map(c => (
                <ListItemButton key={c.id} selected={c.id === selectedId} onClick={() => onSelect(c.id)}
                    sx={{ borderBottom: 1, borderColor: 'divider', alignItems: 'flex-start' }}>
                    <ListItemText
                        primary={
                            <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                                <Typography fontWeight={c.unread_count ? 800 : 600} noWrap>
                                    {c.customer_name || c.contact_name || `+${c.wa_phone}`}
                                </Typography>
                                <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
                                    {describeAge(c.last_message_at)}
                                </Typography>
                            </Stack>
                        }
                        secondary={
                            <Box component="span" sx={{ display: 'block' }}>
                                <Stack direction="row" alignItems="center" spacing={1} component="span">
                                    <Typography component="span" variant="body2" color="text.secondary" noWrap sx={{ flex: 1 }}>
                                        {c.last_message_preview}
                                    </Typography>
                                    {c.unread_count > 0 && <Badge color="primary" badgeContent={c.unread_count} sx={{ mr: 1 }} />}
                                </Stack>
                                <Stack direction="row" spacing={0.5} component="span" sx={{ mt: 0.5, display: 'flex' }}>
                                    {c.needs_attention && c.attention_reason && (
                                        <Chip size="small" color={REASON_META[c.attention_reason]?.color || 'default'}
                                            label={REASON_META[c.attention_reason]?.label || c.attention_reason} />
                                    )}
                                    {c.ai_paused && <Chip size="small" variant="outlined" label="AI paused" />}
                                </Stack>
                            </Box>
                        }
                    />
                </ListItemButton>
            ))}
        </List>
    </Box>
);

export default ConversationList;
