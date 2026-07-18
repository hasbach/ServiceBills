import React, { useEffect, useState, useCallback } from 'react';
import {
    Box, Typography, Table, TableHead, TableRow, TableCell, TableBody,
    Button, Chip, AppBar, Toolbar, CircularProgress, Paper, Alert, Stack,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const SuperAdminView = () => {
    const { apiService, setSnackbar, logout } = useAppContext();
    const [tenants, setTenants] = useState(null);
    const [requests, setRequests] = useState([]);

    const load = useCallback(() => {
        apiService.adminTenants().then((r) => setTenants(r.data)).catch(() => setTenants([]));
        apiService.adminUpgradeRequests().then((r) => setRequests(r.data)).catch(() => setRequests([]));
    }, [apiService]);

    useEffect(() => { load(); }, [load]);

    const act = async (fn, id, label) => {
        try {
            await fn(id);
            setSnackbar({ open: true, message: `Tenant ${label}.`, severity: 'success' });
            load();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Action failed.', severity: 'error' });
        }
    };

    const setPlan = async (id, plan) => {
        try {
            await apiService.adminSetPlan(id, plan);
            setSnackbar({ open: true, message: `Plan set to ${plan}.`, severity: 'success' });
            load();
        } catch (e) {
            setSnackbar({ open: true, message: e.response?.data?.msg || 'Could not set plan.', severity: 'error' });
        }
    };

    const del = (id, name) => {
        if (window.prompt(`Type DELETE to permanently remove "${name}" and ALL its data`) === 'DELETE') {
            act(apiService.adminDeleteTenant, id, 'deleted');
        }
    };

    return (
        <Box sx={{ minHeight: '100vh', bgcolor: 'background.default' }}>
            <AppBar position="static" color="inherit">
                <Toolbar>
                    <Typography variant="h6" sx={{ flexGrow: 1, fontWeight: 800, color: 'primary.main' }}>
                        servicesBills — Platform Admin
                    </Typography>
                    <Button onClick={logout}>Logout</Button>
                </Toolbar>
            </AppBar>
            <Box sx={{ p: { xs: 2, md: 3 } }}>
                {/* Pending "contact us to upgrade" requests */}
                {requests.length > 0 && (
                    <Alert severity="info" sx={{ mb: 3 }}>
                        <Typography sx={{ fontWeight: 700, mb: 1 }}>Pending upgrade requests ({requests.length})</Typography>
                        <Stack spacing={0.5}>
                            {requests.map((r) => (
                                <Box key={r.id} sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, alignItems: 'center' }}>
                                    <strong>{r.tenant_name}</strong> wants <em>{r.requested_plan}</em>
                                    <span>— {r.contact_name || '—'} · {r.contact_email || '—'} · {r.contact_phone || '—'}</span>
                                    {r.message && <span>· "{r.message}"</span>}
                                    <Button size="small" variant="contained"
                                            onClick={() => setPlan(r.tenant_id, r.requested_plan)}>
                                        Approve → set {r.requested_plan}
                                    </Button>
                                </Box>
                            ))}
                        </Stack>
                    </Alert>
                )}

                <Typography variant="h5" sx={{ mb: 2 }}>Tenants</Typography>
                {!tenants ? <CircularProgress /> : (
                    <Paper variant="outlined" sx={{ overflowX: 'auto' }}>
                        <Table size="small">
                            <TableHead>
                                <TableRow>
                                    <TableCell>Name</TableCell>
                                    <TableCell>Plan</TableCell>
                                    <TableCell>Status</TableCell>
                                    <TableCell align="right">Customers</TableCell>
                                    <TableCell align="right">Users</TableCell>
                                    <TableCell align="right">Actions</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {tenants.map((t) => (
                                    <TableRow key={t.id} hover>
                                        <TableCell>{t.name}</TableCell>
                                        <TableCell><Chip size="small" label={t.plan} /></TableCell>
                                        <TableCell>
                                            <Chip size="small" color={t.status === 'active' ? 'success' : 'warning'} label={t.status} />
                                        </TableCell>
                                        <TableCell align="right">{t.customers}</TableCell>
                                        <TableCell align="right">{t.users}</TableCell>
                                        <TableCell align="right">
                                            {t.plan === 'free'
                                                ? <Button size="small" onClick={() => setPlan(t.id, 'pro')}>Set Pro</Button>
                                                : <Button size="small" onClick={() => setPlan(t.id, 'free')}>Set Free</Button>}
                                            {t.status === 'active'
                                                ? <Button size="small" onClick={() => act(apiService.adminSuspendTenant, t.id, 'suspended')}>Suspend</Button>
                                                : <Button size="small" onClick={() => act(apiService.adminReactivateTenant, t.id, 'reactivated')}>Reactivate</Button>}
                                            <Button size="small" color="error" onClick={() => del(t.id, t.name)}>Delete</Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </Paper>
                )}
            </Box>
        </Box>
    );
};

export default SuperAdminView;
