import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Button, Paper, Table, TableHead, TableRow, TableCell, TableBody,
    Chip, IconButton, Tooltip, CircularProgress, Dialog, DialogTitle, DialogContent,
    DialogActions, TextField, MenuItem, Divider, Alert,
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

const EMPTY_TEMPLATE_FORM = {
    name: '', language: 'en', category: 'UTILITY',
    headerType: 'NONE', headerText: '',
    bodyText: '', bodySamples: [],
    footerText: '',
    buttons: [], // [{ type: 'URL'|'PHONE_NUMBER'|'QUICK_REPLY', text, value }]
};

function buildComponents(form) {
    const components = [];
    if (form.headerType === 'TEXT' && form.headerText) {
        components.push({ type: 'HEADER', format: 'TEXT', text: form.headerText });
    } else if (['IMAGE', 'VIDEO', 'DOCUMENT'].includes(form.headerType)) {
        components.push({ type: 'HEADER', format: form.headerType,
            example: form.headerHandle ? { header_handle: [form.headerHandle] } : undefined });
    }
    const bodyComponent = { type: 'BODY', text: form.bodyText };
    if (form.bodySamples.length > 0) {
        bodyComponent.example = { body_text: [form.bodySamples] };
    }
    components.push(bodyComponent);
    if (form.footerText) {
        components.push({ type: 'FOOTER', text: form.footerText });
    }
    if (form.buttons.length > 0) {
        components.push({
            type: 'BUTTONS',
            buttons: form.buttons.map(b => {
                if (b.type === 'URL') return { type: 'URL', text: b.text, url: b.value };
                if (b.type === 'PHONE_NUMBER') return { type: 'PHONE_NUMBER', text: b.text, phone_number: b.value };
                return { type: 'QUICK_REPLY', text: b.text };
            }),
        });
    }
    return components;
}

function countBodyVariables(text) {
    const matches = text.match(/\{\{(\d+)\}\}/g) || [];
    return matches.length;
}

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

    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingTemplate, setEditingTemplate] = useState(null);
    const [form, setForm] = useState(EMPTY_TEMPLATE_FORM);
    const [saving, setSaving] = useState(false);

    const openCreateDialog = () => {
        setEditingTemplate(null);
        setForm(EMPTY_TEMPLATE_FORM);
        setDialogOpen(true);
    };

    const openEditDialog = (template) => {
        setEditingTemplate(template);
        const body = (template.components || []).find(c => c.type === 'BODY') || {};
        const header = (template.components || []).find(c => c.type === 'HEADER') || {};
        const footer = (template.components || []).find(c => c.type === 'FOOTER') || {};
        const buttonsComp = (template.components || []).find(c => c.type === 'BUTTONS') || { buttons: [] };
        setForm({
            name: template.name, language: template.language, category: template.category,
            headerType: header.format || 'NONE', headerText: header.text || '',
            bodyText: body.text || '', bodySamples: (body.example?.body_text?.[0]) || [],
            footerText: footer.text || '',
            buttons: (buttonsComp.buttons || []).map(b => ({
                type: b.type, text: b.text, value: b.url || b.phone_number || '',
            })),
        });
        setDialogOpen(true);
    };

    const addButton = () => setForm(f => ({ ...f, buttons: [...f.buttons, { type: 'QUICK_REPLY', text: '', value: '' }] }));
    const removeButton = (idx) => setForm(f => ({ ...f, buttons: f.buttons.filter((_, i) => i !== idx) }));
    const updateButton = (idx, patch) => setForm(f => ({
        ...f, buttons: f.buttons.map((b, i) => (i === idx ? { ...b, ...patch } : b)),
    }));

    const bodyVarCount = countBodyVariables(form.bodyText);
    useEffect(() => {
        setForm(f => ({
            ...f,
            bodySamples: Array.from({ length: bodyVarCount }, (_, i) => f.bodySamples[i] || ''),
        }));
    }, [bodyVarCount]);

    const handleSaveTemplate = async () => {
        setSaving(true);
        try {
            const components = buildComponents(form);
            if (editingTemplate) {
                await apiService.updateWhatsAppTemplate(editingTemplate.id, { components });
                setSnackbar({ open: true, message: 'Template updated and resubmitted for review.', severity: 'success' });
            } else {
                await apiService.createWhatsAppTemplate({
                    name: form.name, language: form.language, category: form.category, components,
                });
                setSnackbar({ open: true, message: 'Template submitted to Meta for review.', severity: 'success' });
            }
            setDialogOpen(false);
            fetchTemplates();
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to save template.', severity: 'error' });
        } finally {
            setSaving(false);
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
                    <Button variant="contained" startIcon={<AddIcon />} onClick={openCreateDialog}>
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
                                    <IconButton size="small" disabled={t.status === 'APPROVED'} onClick={() => openEditDialog(t)}>
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
            <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
                <DialogTitle>{editingTemplate ? `Edit "${editingTemplate.name}"` : 'New Template'}</DialogTitle>
                <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
                    {!editingTemplate && (
                        <>
                            <TextField label="Name" value={form.name}
                                onChange={e => setForm(f => ({ ...f, name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') }))}
                                helperText="Lowercase letters, numbers, and underscores only" fullWidth />
                            <Box sx={{ display: 'flex', gap: 2 }}>
                                <TextField select label="Category" value={form.category}
                                    onChange={e => setForm(f => ({ ...f, category: e.target.value }))} fullWidth>
                                    <MenuItem value="UTILITY">Utility</MenuItem>
                                    <MenuItem value="MARKETING">Marketing</MenuItem>
                                </TextField>
                                <TextField label="Language" value={form.language}
                                    onChange={e => setForm(f => ({ ...f, language: e.target.value }))}
                                    helperText="e.g. en, ar, fr" fullWidth />
                            </Box>
                        </>
                    )}

                    <Divider />
                    <TextField select label="Header" value={form.headerType}
                        onChange={e => setForm(f => ({ ...f, headerType: e.target.value }))} fullWidth>
                        <MenuItem value="NONE">None</MenuItem>
                        <MenuItem value="TEXT">Text</MenuItem>
                        <MenuItem value="IMAGE">Image</MenuItem>
                        <MenuItem value="VIDEO">Video</MenuItem>
                        <MenuItem value="DOCUMENT">Document</MenuItem>
                    </TextField>
                    {form.headerType === 'TEXT' && (
                        <TextField label="Header Text" value={form.headerText}
                            onChange={e => setForm(f => ({ ...f, headerText: e.target.value }))} fullWidth />
                    )}
                    {['IMAGE', 'VIDEO', 'DOCUMENT'].includes(form.headerType) && (
                        <Button variant="outlined" component="label" size="small" sx={{ alignSelf: 'flex-start' }}>
                            {form.headerHandle ? 'Sample uploaded ✓' : 'Upload Sample File'}
                            <input type="file" hidden onChange={async (e) => {
                                const f = e.target.files[0];
                                if (!f) return;
                                const formData = new FormData();
                                formData.append('file', f);
                                try {
                                    const res = await apiService.uploadWhatsAppTemplateSample(formData);
                                    setForm(prev => ({ ...prev, headerHandle: res.data.header_handle }));
                                    setSnackbar({ open: true, message: 'Sample uploaded.', severity: 'success' });
                                } catch (error) {
                                    setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to upload sample.', severity: 'error' });
                                }
                            }} />
                        </Button>
                    )}

                    <Divider />
                    <TextField label="Body" value={form.bodyText} multiline minRows={3}
                        onChange={e => setForm(f => ({ ...f, bodyText: e.target.value }))}
                        helperText='Use {{1}}, {{2}}... for variables' fullWidth />
                    <Button size="small" sx={{ alignSelf: 'flex-start' }}
                        onClick={() => setForm(f => ({ ...f, bodyText: f.bodyText + `{{${countBodyVariables(f.bodyText) + 1}}}` }))}>
                        Insert Variable
                    </Button>
                    {form.bodySamples.map((sample, idx) => (
                        <TextField key={idx} size="small" label={`Sample value for {{${idx + 1}}}`} value={sample}
                            onChange={e => setForm(f => ({
                                ...f, bodySamples: f.bodySamples.map((s, i) => (i === idx ? e.target.value : s)),
                            }))} fullWidth />
                    ))}

                    <Divider />
                    <TextField label="Footer (optional)" value={form.footerText}
                        onChange={e => setForm(f => ({ ...f, footerText: e.target.value }))} fullWidth />

                    <Divider />
                    <Typography variant="subtitle2">Buttons</Typography>
                    {form.buttons.map((b, idx) => (
                        <Box key={idx} sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
                            <TextField select size="small" value={b.type}
                                onChange={e => updateButton(idx, { type: e.target.value })} sx={{ width: 160 }}>
                                <MenuItem value="QUICK_REPLY">Quick Reply</MenuItem>
                                <MenuItem value="URL">URL</MenuItem>
                                <MenuItem value="PHONE_NUMBER">Phone Number</MenuItem>
                            </TextField>
                            <TextField size="small" label="Label" value={b.text}
                                onChange={e => updateButton(idx, { text: e.target.value })} />
                            {b.type !== 'QUICK_REPLY' && (
                                <TextField size="small" label={b.type === 'URL' ? 'URL' : 'Phone number'} value={b.value}
                                    onChange={e => updateButton(idx, { value: e.target.value })} sx={{ flexGrow: 1 }} />
                            )}
                            <IconButton size="small" onClick={() => removeButton(idx)}><DeleteIcon fontSize="small" /></IconButton>
                        </Box>
                    ))}
                    <Button size="small" sx={{ alignSelf: 'flex-start' }} onClick={addButton}>Add Button</Button>

                    <Alert severity="info">
                        Meta enforces exact limits on button count/combinations and reviews wording for policy
                        compliance — any rejection will show Meta's own message here after you submit.
                    </Alert>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
                    <Button variant="contained" onClick={handleSaveTemplate} disabled={saving || !form.bodyText}>
                        {saving ? <CircularProgress size={20} /> : (editingTemplate ? 'Save & Resubmit' : 'Submit for Review')}
                    </Button>
                </DialogActions>
            </Dialog>
        </Paper>
    );
};

export default WhatsAppTemplatesManager;
