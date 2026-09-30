import React, { useEffect, useState, useCallback } from 'react';
import {
    Box, Typography, Table, TableHead, TableRow, TableCell, TableBody, Button, Chip,
    CircularProgress, Paper, IconButton, Tooltip, Stack,
} from '@mui/material';
import { ContentCopy as ContentCopyIcon } from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import { formatModuleSummary, formatExpiryDay } from '../utils/licenseAdmin.js';
import { CreateLicenseDialog, RenewDialog, EditLicenseDialog } from './LicenseDialogs.js';

const STATUS_COLOR = { active: 'success', trial: 'info', expired: 'warning', revoked: 'error' };
const stamp = (iso) => (iso ? String(iso).replace('T', ' ').slice(0, 16) : null);
const errMsg = (e, fallback) => e.response?.data?.msg || fallback;

const LicensesAdmin = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [rows, setRows] = useState(null);
    const [createOpen, setCreateOpen] = useState(false);
    const [renewTarget, setRenewTarget] = useState(null);
    const [editTarget, setEditTarget] = useState(null);

    const toast = useCallback(
        (message, severity = 'success') => setSnackbar({ open: true, message, severity }),
        [setSnackbar],
    );

    const load = useCallback(() => {
        apiService.adminLicenses().then((r) => setRows(r.data)).catch(() => setRows([]));
    }, [apiService]);
    useEffect(() => { load(); }, [load]);

    const create = async (body) => {
        try { await apiService.adminCreateLicense(body); toast('License created.'); load(); return true; }
        catch (e) { toast(errMsg(e, 'Could not create license.'), 'error'); return false; }
    };
    const update = async (id, body, okMsg = 'License updated.') => {
        try { await apiService.adminUpdateLicense(id, body); toast(okMsg); load(); return true; }
        catch (e) { toast(errMsg(e, 'Could not update license.'), 'error'); return false; }
    };
    const renew = async (id, scope, term) => {
        try {
            const r = await apiService.adminRenewLicense(id, scope, term);
            toast('License renewed.');
            load();
            return r.data;
        } catch (e) { toast(errMsg(e, 'Could not renew license.'), 'error'); return null; }
    };

    const unbind = (l) => {
        if (window.confirm(`Unbind the machine from "${l.business_name}"? It can then be activated on another computer.`)) {
            update(l.id, { unbind_machine: true }, 'Machine unbound.');
        }
    };
    const toggleRevoke = (l) => {
        const verb = l.revoked ? 'Un-revoke' : 'Revoke';
        if (window.confirm(`${verb} the license for "${l.business_name}"?`)) {
            update(l.id, { revoked: !l.revoked }, l.revoked ? 'License un-revoked.' : 'License revoked.');
        }
    };
    const download = async (l) => {
        try {
            const r = await apiService.adminLicenseFile(l.id);
            const url = URL.createObjectURL(new Blob([r.data], { type: 'text/plain' }));
            const a = document.createElement('a');
            a.href = url;
            a.download = `servicebills-${l.license_key}.key`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (e) {
            toast(e.response?.status === 503
                ? 'Signing key is not configured on the server.'
                : 'Could not download license file.', 'error');
        }
    };
    const copy = (key) => {
        try { navigator.clipboard.writeText(key); toast('License key copied.'); }
        catch (e) { toast('Copy failed.', 'error'); }
    };

    return (
        <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', mb: 2 }}>
                <Typography variant="h5" sx={{ flexGrow: 1 }}>On-prem licenses</Typography>
                <Button variant="contained" onClick={() => setCreateOpen(true)}>New license</Button>
            </Box>
            {!rows ? <CircularProgress /> : (
                <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
                    <Table size="small">
                        <TableHead>
                            <TableRow>
                                <TableCell>Business</TableCell>
                                <TableCell>License key</TableCell>
                                <TableCell>Status</TableCell>
                                <TableCell>Base</TableCell>
                                <TableCell>Modules</TableCell>
                                <TableCell>Machine</TableCell>
                                <TableCell>Last refresh</TableCell>
                                <TableCell align="right">Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {rows.length === 0 && (
                                <TableRow><TableCell colSpan={8}>No licenses yet.</TableCell></TableRow>
                            )}
                            {rows.map((l) => (
                                <TableRow key={l.id} hover>
                                    <TableCell>
                                        {l.business_name}
                                        {l.trial && <Chip size="small" color="secondary" label="Lead" sx={{ ml: 1 }} />}
                                        <Typography variant="caption" display="block" color="text.secondary">
                                            {l.owner_phone || ''}
                                        </Typography>
                                    </TableCell>
                                    <TableCell sx={{ whiteSpace: 'nowrap' }}>
                                        <Box component="span" sx={{ fontFamily: 'monospace' }}>{l.license_key}</Box>
                                        <Tooltip title="Copy">
                                            <IconButton size="small" onClick={() => copy(l.license_key)}>
                                                <ContentCopyIcon fontSize="inherit" />
                                            </IconButton>
                                        </Tooltip>
                                    </TableCell>
                                    <TableCell>
                                        <Chip size="small" color={STATUS_COLOR[l.status] || 'default'} label={l.status} />
                                    </TableCell>
                                    <TableCell>{l.base_term} · {formatExpiryDay(l.base_term, l.base_expires_at)}</TableCell>
                                    <TableCell sx={{ maxWidth: 260 }}>{formatModuleSummary(l.modules)}</TableCell>
                                    <TableCell sx={{ fontFamily: 'monospace' }}>
                                        {l.machine_id
                                            ? l.machine_id.slice(0, 8)
                                            : <Box component="span" sx={{ color: 'text.secondary' }}>not activated</Box>}
                                    </TableCell>
                                    <TableCell>
                                        {stamp(l.last_refresh_at) || '—'}
                                        {l.last_app_version && (
                                            <Typography variant="caption" display="block" color="text.secondary">
                                                v{l.last_app_version}
                                            </Typography>
                                        )}
                                    </TableCell>
                                    <TableCell align="right">
                                        <Stack direction="row" justifyContent="flex-end" flexWrap="wrap">
                                            <Button size="small" onClick={() => setRenewTarget(l)}>Renew…</Button>
                                            <Button size="small" onClick={() => setEditTarget(l)}>Edit</Button>
                                            {l.machine_id && <Button size="small" onClick={() => unbind(l)}>Unbind machine</Button>}
                                            <Button size="small" color={l.revoked ? 'primary' : 'error'} onClick={() => toggleRevoke(l)}>
                                                {l.revoked ? 'Un-revoke' : 'Revoke'}
                                            </Button>
                                            <Button size="small" onClick={() => download(l)}>Download .key</Button>
                                        </Stack>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </Paper>
            )}
            <CreateLicenseDialog open={createOpen} onClose={() => setCreateOpen(false)} onSubmit={create} />
            <RenewDialog lic={renewTarget} onClose={() => setRenewTarget(null)} onSubmit={renew} />
            <EditLicenseDialog lic={editTarget} onClose={() => setEditTarget(null)} onSubmit={update} />
        </Box>
    );
};

export default LicensesAdmin;
