import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, TextField, Chip, Table, TableHead, TableRow,
    TableCell, TableBody, IconButton, Tooltip, Alert, CircularProgress, Stack,
} from '@mui/material';
import { ContentCopy as CopyIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import { reasonText, moduleLabel } from '../utils/licenseStatus';

const fmtDate = (v) => (v ? new Date(v).toLocaleDateString() : '—');
const fmtStamp = (v) => (v ? new Date(v).toLocaleString() : 'never');

const ERROR_TEXT = {
    invalid_license: 'That license is not valid.',
    machine_mismatch: 'That license belongs to a different computer.',
    revoked: 'That license has been revoked.',
};

const LicenseTab = ({ setSnackbar }) => {
    const { apiService, refreshSystemInfo, refreshModules } = useAppContext();
    const [lic, setLic] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [key, setKey] = useState('');
    const [error, setError] = useState('');

    const load = useCallback(async () => {
        try {
            const r = await apiService.getLicense();
            setLic(r.data);
        } catch (e) {
            setError(e.response?.data?.msg || 'Could not load license information.');
        } finally {
            setLoading(false);
        }
    }, [apiService]);
    useEffect(() => { load(); }, [load]);

    const run = async (fn, okMessage) => {
        setBusy(true);
        setError('');
        try {
            const r = await fn();
            setLic(r.data);
            await Promise.all([refreshSystemInfo(), refreshModules()]);
            setSnackbar({ open: true, message: okMessage, severity: 'success' });
            return true;
        } catch (e) {
            const msg = e.response?.data?.msg;
            setError(ERROR_TEXT[msg] || msg || 'Something went wrong. Please try again.');
            return false;
        } finally {
            setBusy(false);
        }
    };

    const onFile = (e) => {
        const f = e.target.files[0];
        e.target.value = '';
        if (!f) return;
        const reader = new FileReader();
        reader.onload = () => run(() => apiService.uploadLicenseFile(String(reader.result)), 'License file installed.');
        reader.readAsText(f);
    };

    const copyMachineId = () => {
        try {
            navigator.clipboard.writeText(lic?.machine_id || '');
            setSnackbar({ open: true, message: 'Machine ID copied.', severity: 'info' });
        } catch (e) { /* clipboard unavailable */ }
    };

    if (loading) return <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>;

    const valid = lic?.state === 'valid';
    const modules = Object.entries(lic?.modules || {});

    return (
        <Paper sx={{ p: 3, borderRadius: '16px' }}>
            <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 700 }}>License</Typography>
                {lic && <Chip size="small" color={valid ? 'success' : 'error'}
                    label={valid ? (lic.trial ? 'Trial' : 'Valid') : 'View-only'} />}
            </Stack>
            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
            {lic && !valid && <Alert severity="warning" sx={{ mb: 2 }}>{reasonText(lic.reason)}</Alert>}

            {lic && (
                <>
                    <Typography variant="body2">Licensed to: <strong>{lic.business_name || '—'}</strong></Typography>
                    <Typography variant="body2" sx={{ mb: 2 }}>
                        ServiceBills: <strong>{lic.base ? `${lic.base.term}, expires ${fmtDate(lic.base.expires_at)}` : 'no license installed'}</strong>
                    </Typography>

                    <Table size="small" sx={{ mb: 2 }}>
                        <TableHead>
                            <TableRow><TableCell>Module</TableCell><TableCell>Term</TableCell><TableCell>Expires</TableCell><TableCell>Status</TableCell></TableRow>
                        </TableHead>
                        <TableBody>
                            {modules.length === 0 && <TableRow><TableCell colSpan={4}>No add-on modules.</TableCell></TableRow>}
                            {modules.map(([k, m]) => (
                                <TableRow key={k}>
                                    <TableCell>{moduleLabel(k)}</TableCell>
                                    <TableCell>{m.term}</TableCell>
                                    <TableCell>{fmtDate(m.expires_at)}</TableCell>
                                    <TableCell><Chip size="small" color={m.active ? 'success' : 'default'} label={m.active ? 'Active' : 'Inactive'} /></TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>

                    <Typography variant="body2" color="text.secondary">
                        Last renewal check: {fmtStamp(lic.last_refresh_at)}
                        {lic.last_refresh_error ? ` (error: ${lic.last_refresh_error})` : ''}
                    </Typography>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, my: 1 }}>
                        <Typography variant="body2">Machine ID:</Typography>
                        <Typography variant="body2" sx={{ fontFamily: 'ui-monospace, Consolas, monospace', wordBreak: 'break-all' }}>{lic.machine_id}</Typography>
                        <Tooltip title="Copy"><IconButton size="small" onClick={copyMachineId} aria-label="Copy machine ID"><CopyIcon fontSize="small" /></IconButton></Tooltip>
                    </Box>
                </>
            )}

            <Box sx={{ mt: 3, display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                <TextField size="small" label="License key" placeholder="SB-XXXX-XXXX-XXXX" value={key}
                    onChange={e => setKey(e.target.value)} sx={{ minWidth: 260 }} />
                <Button variant="contained" disabled={busy || !key.trim()}
                    onClick={async () => { if (await run(() => apiService.activateLicense(key.trim()), 'License activated.')) setKey(''); }}>
                    Activate license key
                </Button>
                <Button variant="outlined" component="label" disabled={busy}>
                    Upload license file
                    <input type="file" hidden accept=".key,.txt" onChange={onFile} />
                </Button>
                <Button variant="outlined" disabled={busy}
                    onClick={() => run(() => apiService.refreshLicense(), 'License checked.')}>
                    Check for renewal now
                </Button>
            </Box>
        </Paper>
    );
};

export default LicenseTab;
