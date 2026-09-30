import React, { useState, useEffect } from 'react';
import {
    Box, Typography, Button, CircularProgress, Dialog, DialogTitle, DialogContent,
    DialogActions, TextField, MenuItem, Checkbox, FormControlLabel, Stack,
} from '@mui/material';
import { moduleLabel } from '../utils/licenseStatus.js';
import { TERMS, PAID_MODULE_KEYS, previewExpiry, formatExpiryDay } from '../utils/licenseAdmin.js';

const TermSelect = ({ value, onChange, disabled, label = 'Term' }) => (
    <TextField select size="small" label={label} value={value} disabled={disabled}
               onChange={(e) => onChange(e.target.value)} sx={{ minWidth: 130 }}>
        {TERMS.map((t) => <MenuItem key={t} value={t}>{t}</MenuItem>)}
    </TextField>
);

export const CreateLicenseDialog = ({ open, onClose, onSubmit }) => {
    const [name, setName] = useState('');
    const [phone, setPhone] = useState('');
    const [notes, setNotes] = useState('');
    const [baseTerm, setBaseTerm] = useState('yearly');
    const [mods, setMods] = useState({});
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (open) { setName(''); setPhone(''); setNotes(''); setBaseTerm('yearly'); setMods({}); }
    }, [open]);

    const submit = async () => {
        setBusy(true);
        const modules = {};
        Object.entries(mods).forEach(([k, v]) => { if (v) modules[k] = v; });
        const ok = await onSubmit({
            business_name: name.trim(), owner_phone: phone.trim() || undefined,
            base_term: baseTerm, modules, notes: notes.trim() || undefined,
        });
        setBusy(false);
        if (ok) onClose();
    };

    return (
        <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
            <DialogTitle>New license</DialogTitle>
            <DialogContent>
                <Stack spacing={2} sx={{ mt: 1 }}>
                    <TextField label="Business name" value={name} onChange={(e) => setName(e.target.value)} fullWidth required />
                    <TextField label="Owner phone" value={phone} onChange={(e) => setPhone(e.target.value)} fullWidth />
                    <TextField label="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} fullWidth multiline />
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                        <TermSelect label="ServiceBills term" value={baseTerm} onChange={setBaseTerm} />
                        <Typography variant="caption" color="text.secondary">expires {previewExpiry(baseTerm)}</Typography>
                    </Box>
                    {PAID_MODULE_KEYS.map((k) => (
                        <Box key={k} sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <FormControlLabel sx={{ flex: 1 }} label={moduleLabel(k)}
                                control={<Checkbox checked={!!mods[k]}
                                    onChange={(e) => setMods((m) => ({ ...m, [k]: e.target.checked ? 'yearly' : null }))} />} />
                            {mods[k] && (
                                <>
                                    <TermSelect value={mods[k]} onChange={(v) => setMods((m) => ({ ...m, [k]: v }))} />
                                    <Typography variant="caption" color="text.secondary" sx={{ width: 90 }}>{previewExpiry(mods[k])}</Typography>
                                </>
                            )}
                        </Box>
                    ))}
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={busy}>Cancel</Button>
                <Button variant="contained" onClick={submit} disabled={busy || !name.trim()}>
                    {busy ? <CircularProgress size={20} /> : 'Create'}
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export const RenewDialog = ({ lic, onClose, onSubmit }) => {
    const [scope, setScope] = useState('base');
    const [term, setTerm] = useState('yearly');
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null);

    const current = (s) => (s === 'base'
        ? { term: lic?.base_term, expires_at: lic?.base_expires_at }
        : lic?.modules?.[s]);

    useEffect(() => {
        if (lic) {
            setScope('base');
            setResult(null);
            setTerm(TERMS.includes(lic.base_term) ? lic.base_term : 'yearly');
        }
    }, [lic]);

    const pick = (s) => {
        setScope(s);
        const c = current(s);
        setTerm(c && TERMS.includes(c.term) ? c.term : 'yearly');
    };

    const submit = async () => {
        setBusy(true);
        const row = await onSubmit(lic.id, scope, term);
        setBusy(false);
        if (row) {
            const after = scope === 'base'
                ? { term: row.base_term, expires_at: row.base_expires_at }
                : row.modules?.[scope];
            setResult({ before: current(scope), after });
        }
    };

    const fmt = (c) => (c ? `${c.term} · ${formatExpiryDay(c.term, c.expires_at)}` : 'none');

    return (
        <Dialog open={!!lic} onClose={busy ? undefined : onClose} fullWidth maxWidth="xs">
            <DialogTitle>Renew — {lic?.business_name}</DialogTitle>
            <DialogContent>
                <Stack spacing={2} sx={{ mt: 1 }}>
                    <TextField select size="small" label="What to renew" value={scope}
                               onChange={(e) => pick(e.target.value)} disabled={!!result}>
                        <MenuItem value="base">ServiceBills (base)</MenuItem>
                        {PAID_MODULE_KEYS.map((k) => <MenuItem key={k} value={k}>{moduleLabel(k)}</MenuItem>)}
                    </TextField>
                    <TermSelect value={term} onChange={setTerm} disabled={!!result} />
                    {result && (
                        <Typography variant="body2">
                            {fmt(result.before)} → <strong>{fmt(result.after)}</strong>
                        </Typography>
                    )}
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={busy}>{result ? 'Close' : 'Cancel'}</Button>
                {!result && (
                    <Button variant="contained" onClick={submit} disabled={busy}>
                        {busy ? <CircularProgress size={20} /> : 'Renew'}
                    </Button>
                )}
            </DialogActions>
        </Dialog>
    );
};

const toDate = (iso) => (iso ? String(iso).slice(0, 10) : '');

export const EditLicenseDialog = ({ lic, onClose, onSubmit }) => {
    const [f, setF] = useState({});
    const [mods, setMods] = useState({});
    const [removed, setRemoved] = useState({});
    const [busy, setBusy] = useState(false);

    useEffect(() => {
        if (!lic) return;
        setF({
            business_name: lic.business_name || '', owner_phone: lic.owner_phone || '',
            notes: lic.notes || '', base_expires_at: toDate(lic.base_expires_at),
        });
        const m = {};
        Object.entries(lic.modules || {}).forEach(([k, v]) => { m[k] = toDate(v.expires_at); });
        setMods(m);
        setRemoved({});
    }, [lic]);

    const submit = async () => {
        const body = { business_name: f.business_name.trim(), owner_phone: f.owner_phone.trim(), notes: f.notes };
        if (f.base_expires_at && f.base_expires_at !== toDate(lic.base_expires_at)) {
            body.base_expires_at = `${f.base_expires_at}T00:00:00Z`;
        }
        const modules = {};
        Object.entries(lic.modules || {}).forEach(([k, v]) => {
            if (removed[k]) modules[k] = null;
            else if (mods[k] && mods[k] !== toDate(v.expires_at)) {
                modules[k] = { term: v.term, expires_at: `${mods[k]}T00:00:00Z` };
            }
        });
        if (Object.keys(modules).length) body.modules = modules;
        setBusy(true);
        const ok = await onSubmit(lic.id, body);
        setBusy(false);
        if (ok) onClose();
    };

    return (
        <Dialog open={!!lic} onClose={busy ? undefined : onClose} fullWidth maxWidth="sm">
            <DialogTitle>Edit license — {lic?.license_key}</DialogTitle>
            <DialogContent>
                <Stack spacing={2} sx={{ mt: 1 }}>
                    <TextField label="Business name" value={f.business_name || ''}
                               onChange={(e) => setF({ ...f, business_name: e.target.value })} fullWidth />
                    <TextField label="Owner phone" value={f.owner_phone || ''}
                               onChange={(e) => setF({ ...f, owner_phone: e.target.value })} fullWidth />
                    <TextField label="Notes" value={f.notes || ''}
                               onChange={(e) => setF({ ...f, notes: e.target.value })} fullWidth multiline />
                    <TextField label="ServiceBills expires on" type="date" value={f.base_expires_at || ''}
                               onChange={(e) => setF({ ...f, base_expires_at: e.target.value })}
                               InputLabelProps={{ shrink: true }} disabled={lic?.base_term === 'lifetime'} />
                    {Object.entries(lic?.modules || {}).map(([k, v]) => (
                        <Box key={k} sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                            <Typography sx={{ flex: 1 }}>{moduleLabel(k)} ({v.term})</Typography>
                            <TextField size="small" type="date" label="Expires on" value={mods[k] || ''}
                                       disabled={!!removed[k] || v.term === 'lifetime'}
                                       onChange={(e) => setMods({ ...mods, [k]: e.target.value })}
                                       InputLabelProps={{ shrink: true }} />
                            <Button size="small" color="error" onClick={() => setRemoved({ ...removed, [k]: !removed[k] })}>
                                {removed[k] ? 'Keep' : 'Remove'}
                            </Button>
                        </Box>
                    ))}
                </Stack>
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={busy}>Cancel</Button>
                <Button variant="contained" onClick={submit} disabled={busy || !(f.business_name || '').trim()}>
                    {busy ? <CircularProgress size={20} /> : 'Save'}
                </Button>
            </DialogActions>
        </Dialog>
    );
};
