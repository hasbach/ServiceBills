import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Button, Paper, Table, TableHead, TableRow, TableCell, TableBody,
    Chip, IconButton, Tooltip, CircularProgress,
} from '@mui/material';
import { Add as AddIcon, Refresh as RefreshIcon, Edit as EditIcon, Delete as DeleteIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';

const STATUS_COLOR = {
    APPROVED: 'success',
    PENDING: 'default',
    REJECTED: 'error',
    PAUSED: 'warning',
    DISABLED: 'warning',
};

const WhatsAppTemplatesManager = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [templates, setTemplates] = useState([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);

    const fetchTemplates = useCallback(async () => {
        setLoading(true);
        try {
            const res = await apiService.fetchWhatsAppTemplates();
            setTemplates(res.data.templates || []);
        } catch (error) {
            setSnackbar({ open: true, message: 'Failed to load templates.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [apiService, setSnackbar]);

    useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

    const handleSync = async () => {
        setSyncing(true);
        try {
            const res = await apiService.syncWhatsAppTemplates();
            setTemplates(res.data.templates || []);
            setSnackbar({ open: true, message: res.data.message || 'Synced.', severity: 'success' });
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to sync from Meta.', severity: 'error' });
        } finally {
            setSyncing(false);
        }
    };

    const handleDelete = async (template) => {
        if (!window.confirm(`Delete template "${template.name}"? This also deletes it from Meta.`)) return;
        try {
            await apiService.deleteWhatsAppTemplate(template.id);
            setSnackbar({ open: true, message: 'Template deleted.', severity: 'success' });
            fetchTemplates();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to delete template.', severity: 'error' });
        }
    };

    return (
        <Paper sx={{ p: 3, mt: 4 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
                <Typography variant="h6">WhatsApp Templates</Typography>
                <Box sx={{ display: 'flex', gap: 1 }}>
                    <Button variant="outlined" startIcon={syncing ? <CircularProgress size={16} /> : <RefreshIcon />}
                        onClick={handleSync} disabled={syncing}>
                        Refresh from Meta
                    </Button>
                    <Button variant="contained" startIcon={<AddIcon />} disabled>
                        New Template
                    </Button>
                </Box>
            </Box>
            {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}><CircularProgress /></Box>
            ) : (
                <Table size="small">
                    <TableHead>
                        <TableRow>
                            <TableCell>Name</TableCell>
                            <TableCell>Category</TableCell>
                            <TableCell>Language</TableCell>
                            <TableCell>Status</TableCell>
                            <TableCell align="right">Actions</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {templates.map((t) => (
                            <TableRow key={t.id}>
                                <TableCell>{t.name}</TableCell>
                                <TableCell>{t.category}</TableCell>
                                <TableCell>{t.language}</TableCell>
                                <TableCell>
                                    <Tooltip title={t.status === 'REJECTED' ? (t.rejected_reason || '') : ''}>
                                        <Chip size="small" label={t.status} color={STATUS_COLOR[t.status] || 'default'} />
                                    </Tooltip>
                                </TableCell>
                                <TableCell align="right">
                                    <IconButton size="small" disabled={t.status === 'APPROVED'}>
                                        <EditIcon fontSize="small" />
                                    </IconButton>
                                    <IconButton size="small" onClick={() => handleDelete(t)}>
                                        <DeleteIcon fontSize="small" color="error" />
                                    </IconButton>
                                </TableCell>
                            </TableRow>
                        ))}
                        {templates.length === 0 && (
                            <TableRow><TableCell colSpan={5} align="center">No templates yet.</TableCell></TableRow>
                        )}
                    </TableBody>
                </Table>
            )}
        </Paper>
    );
};

export default WhatsAppTemplatesManager;
