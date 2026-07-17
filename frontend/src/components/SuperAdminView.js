import React, { useEffect, useState, useCallback } from 'react';
import {
    Box, Typography, Table, TableHead, TableRow, TableCell, TableBody,
    Button, Chip, AppBar, Toolbar, CircularProgress, Paper,
} from '@mui/material';
import { useAppContext } from '../context/AppContext.js';

const SuperAdminView = () => {
    const { apiService, setSnackbar, logout } = useAppContext();
    const [tenants, setTenants] = useState(null);

    const load = useCallback(() => {
        apiService.adminTenants().then((r) => setTenants(r.data)).catch(() => setTenants([]));
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
