import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
    Dialog, DialogTitle, DialogContent, DialogActions, Button, Box, Typography,
    Checkbox, Chip, TextField, Paper, Collapse, IconButton, Menu, MenuItem,
    Alert, CircularProgress, FormControlLabel, Switch, Stack, useMediaQuery, useTheme,
} from '@mui/material';
import {
    ExpandMore as ExpandMoreIcon,
    ExpandLess as ExpandLessIcon,
    Add as AddIcon,
    CheckCircle as CheckCircleIcon,
    ErrorOutline as ErrorOutlineIcon,
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import { validateItem, insertAt, placeholder, countPlaceholders, LIMITS } from './standardTemplateValidation';

const STATUS_COLOR = {
    APPROVED: 'success',
    PENDING: 'default',
    REJECTED: 'error',
    PAUSED: 'warning',
    DISABLED: 'warning',
};

const fieldSx = { '& .MuiOutlinedInput-root': { borderRadius: '12px' } };

const toItem = (t) => ({
    key: t.key,
    name: t.name || '',
    existingName: t.existing ? t.name : null,
    category: t.category,
    language: t.language || null,
    header: t.header || '',
    body: t.body || '',
    footer: t.footer || '',
    buttons: Array.isArray(t.buttons) ? t.buttons.map(b => ({ ...b })) : [],
    variables: Array.isArray(t.variables) ? t.variables : [],
    buttonVariables: Array.isArray(t.button_variables) ? t.button_variables : [],
    fixed_order: Boolean(t.fixed_order),
    existing: t.existing || null,
    selected: true,
    use_existing: Boolean(t.existing),
});

const errText = (err, fallback) => err?.response?.data?.error || err?.response?.data?.msg || err?.message || fallback;

const StandardTemplatesDialog = ({ open, onClose, onSubmitted }) => {
    const { apiService } = useAppContext();
    const theme = useTheme();
    const fullScreen = useMediaQuery(theme.breakpoints.down('sm'));

    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState('');
    const [expanded, setExpanded] = useState({});
    const [submitting, setSubmitting] = useState(false);
    const [results, setResults] = useState(null); // { key: {ok, status, error} }
    const [submitError, setSubmitError] = useState('');
    const [menu, setMenu] = useState(null); // { anchor, key }

    // Input elements and last caret position per template, so "Insert
    // variable" drops the placeholder where the tenant was typing.
    const inputRefs = useRef({});
    const lastFocus = useRef({});

    const load = useCallback(async () => {
        setLoading(true);
        setLoadError('');
        setResults(null);
        setSubmitError('');
        try {
            const res = await apiService.getStandardTemplates();
            const list = (res.data?.templates || []).map(toItem);
            setItems(list);
            setExpanded({});
        } catch (err) {
            setLoadError(errText(err, 'Could not load the standard templates.'));
        } finally {
            setLoading(false);
        }
    }, [apiService]);

    useEffect(() => { if (open) load(); }, [open, load]);

    const validation = useMemo(() => {
        const out = {};
        items.forEach(i => { out[i.key] = validateItem(i); });
        return out;
    }, [items]);

    const selected = items.filter(i => i.selected);
    const invalidSelected = selected.filter(i => validation[i.key]?.errors.length);
    const canSubmit = selected.length > 0 && invalidSelected.length === 0 && !submitting && !loading;

    const update = (key, patch) => setItems(list => list.map(i => (i.key === key ? { ...i, ...patch } : i)));
    const updateButton = (key, idx, text) => setItems(list => list.map(i => {
        if (i.key !== key) return i;
        const buttons = i.buttons.map((b, j) => (j === idx ? { ...b, text } : b));
        return { ...i, buttons };
    }));

    const rememberCaret = (key, field) => (e) => {
        if (field !== 'body') return;
        const el = e.target;
        lastFocus.current[key] = { start: el.selectionStart, end: el.selectionEnd };
    };

    const readField = (item, field) => {
        if (field.startsWith('button_')) return item.buttons[Number(field.slice(7))]?.text || '';
        return item[field] || '';
    };

    const handleInsert = (key, varKey) => {
        setMenu(null);
        const item = items.find(i => i.key === key);
        if (!item) return;
        // Variables live in the body only (Meta + server rule); insert at the
        // body's last caret position, or at the end if it was never focused.
        const focus = lastFocus.current[key] || {};
        const { text, caret } = insertAt(item.body, focus.start, focus.end, varKey);
        update(key, { body: text });
        lastFocus.current[key] = { start: caret, end: caret };
        // Put the caret back after the inserted placeholder once React re-renders.
        window.requestAnimationFrame(() => {
            const el = inputRefs.current[`${key}:body`];
            if (el) {
                el.focus();
                try { el.setSelectionRange(caret, caret); } catch { /* not a text input */ }
            }
        });
    };

    const handleSubmit = async () => {
        setSubmitting(true);
        setSubmitError('');
        try {
            const payload = {
                // Omitted when the catalog has no language: the server then
                // uses the tenant's Template Language Code.
                language: selected.find(i => i.language)?.language || undefined,
                items: selected.map(i => ({
                    key: i.key,
                    name: (i.use_existing && i.existing) ? (i.existingName || i.name) : i.name.trim(),
                    header: i.header && i.header.trim() ? i.header : null,
                    body: i.body,
                    footer: i.footer && i.footer.trim() ? i.footer : null,
                    buttons: i.buttons,
                    use_existing: Boolean(i.use_existing && i.existing),
                })),
            };
            const res = await apiService.submitStandardTemplates(payload);
            const map = {};
            (res.data?.results || []).forEach(r => { map[r.key] = r; });
            setResults(map);
            // Leave failed items selected (and open) so they can be fixed and resubmitted.
            setItems(list => list.map(i => (map[i.key]?.ok ? { ...i, selected: false } : i)));
            setExpanded(prev => {
                const next = { ...prev };
                Object.values(map).forEach(r => { if (!r.ok) next[r.key] = true; });
                return next;
            });
            if (onSubmitted) onSubmitted(res.data?.results || []);
        } catch (err) {
            setSubmitError(errText(err, 'Submitting the templates failed.'));
        } finally {
            setSubmitting(false);
        }
    };

    const okCount = results ? Object.values(results).filter(r => r.ok).length : 0;
    const failCount = results ? Object.values(results).filter(r => !r.ok).length : 0;

    const counter = (value, max) => `${(value || '').length}/${max}`;

    const renderEditor = (item) => {
        const v = validation[item.key] || { errors: [], counts: {} };
        const counts = countPlaceholders(item.body);
        const textField = (field, label, max, extra = {}) => (
            <TextField
                fullWidth
                size="small"
                label={label}
                value={readField(item, field)}
                onChange={(e) => {
                    if (field.startsWith('button_')) updateButton(item.key, Number(field.slice(7)), e.target.value);
                    else update(item.key, { [field]: e.target.value });
                    rememberCaret(item.key, field)(e);
                }}
                onSelect={rememberCaret(item.key, field)}
                onFocus={rememberCaret(item.key, field)}
                inputRef={(el) => { inputRefs.current[`${item.key}:${field}`] = el; }}
                helperText={counter(readField(item, field), max)}
                error={readField(item, field).length > max}
                FormHelperTextProps={{ sx: { textAlign: 'right', mr: 0 } }}
                sx={fieldSx}
                {...extra}
            />
        );
        return (
            <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                <TextField
                    fullWidth size="small" label="Template name"
                    value={item.name}
                    onChange={(e) => update(item.key, { name: e.target.value })}
                    helperText="Lowercase letters, numbers and _ only"
                    sx={fieldSx}
                />
                <Box>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                        Variables go in the body, each exactly once. Click where you want one, then insert it:
                    </Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75, alignItems: 'center' }}>
                        {item.variables.map(vr => {
                            const n = counts[vr.key] || 0;
                            return (
                                <Chip
                                    key={vr.key}
                                    size="small"
                                    label={`${vr.label || vr.key} ${placeholder(vr.key)}`}
                                    color={n === 1 ? 'success' : n === 0 ? 'error' : 'warning'}
                                    variant={n === 1 ? 'outlined' : 'filled'}
                                    onClick={() => handleInsert(item.key, vr.key)}
                                    sx={{ maxWidth: '100%' }}
                                />
                            );
                        })}
                        {item.variables.length > 0 && (
                            <Button
                                size="small"
                                startIcon={<AddIcon />}
                                onClick={(e) => setMenu({ anchor: e.currentTarget, key: item.key })}
                                sx={{ textTransform: 'none' }}
                            >
                                Insert variable
                            </Button>
                        )}
                        {item.variables.length === 0 && (
                            <Typography variant="caption" color="text.secondary">This template has no variables.</Typography>
                        )}
                    </Box>
                    {item.fixed_order && item.variables.length > 1 && (
                        <Typography variant="caption" color="warning.dark" sx={{ display: 'block', mt: 0.5 }}>
                            Keep these variables in this order; you can change the words around them.
                        </Typography>
                    )}
                </Box>
                {textField('header', 'Header (optional)', LIMITS.header)}
                {textField('body', 'Body', LIMITS.body, { multiline: true, minRows: 3 })}
                {textField('footer', 'Footer (optional)', LIMITS.footer)}
                {item.buttons.map((b, idx) => (
                    <Box key={idx}>
                        {textField(`button_${idx}`, `Button ${idx + 1} text${b.type ? ` (${b.type.replace('_', ' ').toLowerCase()})` : ''}`, LIMITS.button)}
                        {b.type === 'URL' && item.buttonVariables.length > 0 && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: -0.5 }}>
                                The button opens the customer's {item.buttonVariables.map(v => (v.label || v.key).toLowerCase()).join(', ')}, added automatically.
                            </Typography>
                        )}
                    </Box>
                ))}
                {v.errors.length > 0 && (
                    <Alert severity="error" sx={{ borderRadius: '12px', py: 0 }}>
                        <Box component="ul" sx={{ m: 0, pl: 2 }}>
                            {v.errors.map(e => <li key={e}>{e}</li>)}
                        </Box>
                    </Alert>
                )}
            </Stack>
        );
    };

    const menuItem = menu ? items.find(i => i.key === menu.key) : null;

    return (
        <Dialog open={open} onClose={submitting ? undefined : onClose} fullWidth maxWidth="md" fullScreen={fullScreen}>
            <DialogTitle sx={{ fontWeight: 700, pb: 1 }}>Submit standard templates</DialogTitle>
            <DialogContent dividers sx={{ px: { xs: 1.5, sm: 3 } }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Automatic messages need templates approved by Meta. Review the wording, choose which ones to submit,
                    and keep every variable in the text. You can move a variable inside the sentence, but not remove it.
                    Nothing is sent to Meta until you click Submit.
                </Typography>

                {loading && <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>}
                {!loading && loadError && (
                    <Alert severity="error" sx={{ borderRadius: '12px' }}
                        action={<Button color="inherit" size="small" onClick={load}>Retry</Button>}>
                        {loadError}
                    </Alert>
                )}
                {results && (
                    <Alert severity={failCount ? 'warning' : 'success'} sx={{ mb: 2, borderRadius: '12px' }}>
                        {okCount} submitted{failCount ? `, ${failCount} not submitted. Fix them below and submit again.` : '. Meta reviews them, and the status shows in the WhatsApp Templates tab.'}
                    </Alert>
                )}
                {submitError && <Alert severity="error" sx={{ mb: 2, borderRadius: '12px' }}>{submitError}</Alert>}

                {!loading && !loadError && (
                    <Stack spacing={1.5}>
                        {items.map(item => {
                            const v = validation[item.key] || { errors: [] };
                            const r = results?.[item.key];
                            const isOpen = Boolean(expanded[item.key]);
                            const editable = !(item.use_existing && item.existing);
                            return (
                                <Paper key={item.key} variant="outlined" sx={{
                                    p: { xs: 1.25, sm: 2 }, borderRadius: '14px',
                                    borderColor: item.selected && v.errors.length ? 'error.light' : undefined,
                                }}>
                                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                                        <Checkbox
                                            checked={item.selected}
                                            onChange={(e) => update(item.key, { selected: e.target.checked })}
                                            inputProps={{ 'aria-label': `Submit ${item.name}` }}
                                            sx={{ p: 0.5 }}
                                        />
                                        <Box sx={{ flex: 1, minWidth: 0 }}>
                                            <Typography variant="body2" sx={{ fontWeight: 700, wordBreak: 'break-all' }}>{item.name || item.key}</Typography>
                                            <Typography variant="caption" color="text.secondary">{item.category}{item.language ? ` · ${item.language}` : ''}</Typography>
                                        </Box>
                                        {item.existing && (
                                            <Chip size="small" label={item.existing.status || 'EXISTS'} color={STATUS_COLOR[item.existing.status] || 'default'} />
                                        )}
                                        {item.selected && v.errors.length > 0 && (
                                            <Chip size="small" color="error" variant="outlined" icon={<ErrorOutlineIcon />} label={`${v.errors.length} issue${v.errors.length > 1 ? 's' : ''}`} />
                                        )}
                                        {r && (r.ok
                                            ? <Chip size="small" color="success" icon={<CheckCircleIcon />} label={r.status || 'Submitted'} />
                                            : <Chip size="small" color="error" label="Not submitted" />)}
                                        {editable && (
                                            <IconButton size="small" onClick={() => setExpanded(p => ({ ...p, [item.key]: !isOpen }))}
                                                aria-label={isOpen ? 'Collapse' : 'Edit text'}>
                                                {isOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
                                            </IconButton>
                                        )}
                                    </Box>
                                    {r && !r.ok && r.error && (
                                        <Alert severity="error" sx={{ mt: 1, borderRadius: '12px', py: 0 }}>{r.error}</Alert>
                                    )}
                                    {item.existing && (
                                        <FormControlLabel
                                            sx={{ ml: 0, mt: 0.5 }}
                                            control={<Switch size="small" checked={Boolean(item.use_existing)}
                                                onChange={(e) => {
                                                    update(item.key, { use_existing: e.target.checked });
                                                    if (!e.target.checked) setExpanded(p => ({ ...p, [item.key]: true }));
                                                }} />}
                                            label={<Typography variant="body2">Use the existing template (don't submit a new one)</Typography>}
                                        />
                                    )}
                                    {editable && (
                                        <Collapse in={isOpen} unmountOnExit>
                                            {renderEditor(item)}
                                        </Collapse>
                                    )}
                                    {editable && !isOpen && item.selected && v.errors.length > 0 && (
                                        <Typography variant="caption" color="error" sx={{ display: 'block', mt: 0.5 }}>
                                            {v.errors[0]}{v.errors.length > 1 ? ` (+${v.errors.length - 1} more)` : ''}
                                        </Typography>
                                    )}
                                </Paper>
                            );
                        })}
                        {items.length === 0 && (
                            <Typography variant="body2" color="text.secondary">No standard templates are available.</Typography>
                        )}
                    </Stack>
                )}

                <Menu anchorEl={menu?.anchor} open={Boolean(menu)} onClose={() => setMenu(null)}>
                    {(menuItem?.variables || []).map(vr => (
                        <MenuItem key={vr.key} onClick={() => handleInsert(menu.key, vr.key)}>
                            <Box>
                                <Typography variant="body2">{vr.label || vr.key}</Typography>
                                <Typography variant="caption" color="text.secondary">{placeholder(vr.key)}</Typography>
                            </Box>
                        </MenuItem>
                    ))}
                </Menu>
            </DialogContent>
            <DialogActions sx={{ px: { xs: 1.5, sm: 3 }, py: 1.5, flexWrap: 'wrap', gap: 1 }}>
                {invalidSelected.length > 0 && (
                    <Typography variant="caption" color="error" sx={{ mr: 'auto' }}>
                        Fix {invalidSelected.length} selected template{invalidSelected.length > 1 ? 's' : ''} or untick {invalidSelected.length > 1 ? 'them' : 'it'}.
                    </Typography>
                )}
                <Button onClick={onClose} disabled={submitting} sx={{ textTransform: 'none' }}>
                    {results ? 'Close' : 'Cancel'}
                </Button>
                <Button variant="contained" onClick={handleSubmit} disabled={!canSubmit}
                    startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : null}
                    sx={{ textTransform: 'none', fontWeight: 600, borderRadius: '10px' }}>
                    {submitting ? 'Submitting…' : `Submit ${selected.length} template${selected.length === 1 ? '' : 's'}`}
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default StandardTemplatesDialog;
