import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Button, TextField, CircularProgress,
    IconButton, Table, TableBody, TableCell, TableContainer, TableHead,
    TableRow, Dialog, DialogTitle, DialogContent, DialogActions,
    MenuItem, alpha, useTheme, Chip, FormControl, InputLabel, Select, OutlinedInput, Checkbox, ListItemText
} from '@mui/material';
import {
    Add as AddIcon,
    Edit as EditIcon,
    Delete as DeleteIcon,
    People as PeopleIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';

const UserManagement = () => {
    const { apiService, setSnackbar, user: currentUser } = useAppContext();
    const theme = useTheme();
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editMode, setEditMode] = useState(false);
    const [formData, setFormData] = useState({ id: null, username: '', password: '', role: ['employee'] });

    const fetchUsers = useCallback(async () => {
        setLoading(true);
        try {
            const response = await apiService.fetchUsers();
            setUsers(response.data);
        } catch (error) {
            console.error('Failed to fetch users', error);
            setSnackbar({ open: true, message: 'Failed to load users', severity: 'error' });
        } finally {
            setLoading(false);
        }
    }, [apiService, setSnackbar]);

    useEffect(() => {
        fetchUsers();
    }, [fetchUsers]);

    const handleOpenDialog = (user = null) => {
        if (user) {
            setEditMode(true);
            setFormData({ id: user.id, username: user.username, password: '', role: user.role ? user.role.split(',').map(r => r.trim()) : [] });
        } else {
            setEditMode(false);
            setFormData({ id: null, username: '', password: '', role: ['employee'] });
        }
        setDialogOpen(true);
    };

    const handleCloseDialog = () => {
        setDialogOpen(false);
    };

    const handleSaveUser = async () => {
        if (!formData.username) {
            setSnackbar({ open: true, message: 'Username is required', severity: 'warning' });
            return;
        }
        if (!editMode && !formData.password) {
            setSnackbar({ open: true, message: 'Password is required for new users', severity: 'warning' });
            return;
        }

        try {
            if (editMode) {
                const data = { role: formData.role.join(',') };
                if (formData.password) data.password = formData.password;
                await apiService.updateUser(formData.id, data);
                setSnackbar({ open: true, message: 'User updated successfully', severity: 'success' });
            } else {
                await apiService.createUser({ username: formData.username, password: formData.password, role: formData.role.join(',') });
                setSnackbar({ open: true, message: 'User created successfully', severity: 'success' });
            }
            handleCloseDialog();
            fetchUsers();
        } catch (error) {
            const msg = error.response?.data?.msg || 'Failed to save user';
            setSnackbar({ open: true, message: msg, severity: 'error' });
        }
    };

    const handleDeleteUser = async (userId) => {
        if (window.confirm("Are you sure you want to delete this user?")) {
            try {
                await apiService.deleteUser(userId);
                setSnackbar({ open: true, message: 'User deleted successfully', severity: 'success' });
                fetchUsers();
            } catch (error) {
                const msg = error.response?.data?.msg || 'Failed to delete user';
                setSnackbar({ open: true, message: msg, severity: 'error' });
            }
        }
    };

    const getRoleColor = (role) => {
        switch (role) {
            case 'admin': return theme.palette.error.main;
            case 'finance': return theme.palette.success.main;
            case 'employee': return theme.palette.info.main;
            case 'cashier': return theme.palette.warning.main;
            default: return theme.palette.text.secondary;
        }
    };

    return (
        <Paper elevation={0} sx={{
            p: 3, mb: 3, borderRadius: '20px',
            background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
            border: `1px solid ${alpha(theme.palette.primary.main, 0.12)}`
        }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                    <Box sx={{ width: 44, height: 44, borderRadius: '14px', bgcolor: alpha(theme.palette.primary.main, 0.1), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <PeopleIcon sx={{ color: theme.palette.primary.main, fontSize: 22 }} />
                    </Box>
                    <Box>
                        <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>User Management</Typography>
                        <Typography variant="caption" color="text.secondary">Manage app access for administrators, finance, and employees</Typography>
                    </Box>
                </Box>
                <Button variant="contained" startIcon={<AddIcon />} onClick={() => handleOpenDialog()} sx={{ borderRadius: '12px', textTransform: 'none', fontWeight: 600 }}>
                    Add User
                </Button>
            </Box>

            {loading ? (
                <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}><CircularProgress /></Box>
            ) : (
                <TableContainer component={Paper} elevation={0} sx={{ border: `1px solid ${alpha(theme.palette.divider, 0.1)}`, borderRadius: '12px' }}>
                    <Table size="small">
                        <TableHead sx={{ bgcolor: alpha(theme.palette.primary.main, 0.03) }}>
                            <TableRow>
                                <TableCell sx={{ fontWeight: 600 }}>Username</TableCell>
                                <TableCell sx={{ fontWeight: 600 }}>Role</TableCell>
                                <TableCell sx={{ fontWeight: 600, textAlign: 'right' }}>Actions</TableCell>
                            </TableRow>
                        </TableHead>
                        <TableBody>
                            {users.map(user => (
                                <TableRow key={user.id}>
                                    <TableCell>{user.username} {user.id === currentUser?.id ? ' (You)' : ''}</TableCell>
                                    <TableCell>
                                        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
                                            {(user.role ? user.role.split(',') : []).map(r => r.trim()).map(roleStr => (
                                                <Chip 
                                                    key={roleStr}
                                                    label={roleStr} 
                                                    size="small"
                                                    sx={{ 
                                                        bgcolor: alpha(getRoleColor(roleStr), 0.1), 
                                                        color: getRoleColor(roleStr), 
                                                        fontWeight: 600, 
                                                        textTransform: 'capitalize',
                                                        border: `1px solid ${alpha(getRoleColor(roleStr), 0.2)}`
                                                    }} 
                                                />
                                            ))}
                                        </Box>
                                    </TableCell>
                                    <TableCell align="right">
                                        <IconButton size="small" onClick={() => handleOpenDialog(user)} sx={{ color: theme.palette.primary.main }}>
                                            <EditIcon fontSize="small" />
                                        </IconButton>
                                        <IconButton size="small" onClick={() => handleDeleteUser(user.id)} sx={{ color: theme.palette.error.main }} disabled={user.username === currentUser?.username}>
                                            <DeleteIcon fontSize="small" />
                                        </IconButton>
                                    </TableCell>
                                </TableRow>
                            ))}
                            {users.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={3} align="center" sx={{ py: 3, color: 'text.secondary' }}>No users found.</TableCell>
                                </TableRow>
                            )}
                        </TableBody>
                    </Table>
                </TableContainer>
            )}

            <Dialog open={dialogOpen} onClose={handleCloseDialog} PaperProps={{ sx: { borderRadius: '16px', minWidth: '400px' } }}>
                <DialogTitle sx={{ fontWeight: 700 }}>
                    {editMode ? 'Edit User' : 'Add New User'}
                </DialogTitle>
                <DialogContent>
                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
                        <TextField
                            label="Username"
                            fullWidth
                            value={formData.username}
                            onChange={(e) => setFormData({ ...formData, username: e.target.value })}
                            disabled={editMode}
                        />
                        <TextField
                            label={editMode ? "New Password (leave blank to keep current)" : "Password"}
                            type="password"
                            fullWidth
                            value={formData.password}
                            onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                        />
                        <FormControl fullWidth>
                            <InputLabel id="role-select-label">Roles</InputLabel>
                            <Select
                                labelId="role-select-label"
                                multiple
                                value={formData.role}
                                onChange={(e) => {
                                    const { target: { value } } = e;
                                    setFormData({ ...formData, role: typeof value === 'string' ? value.split(',') : value });
                                }}
                                input={<OutlinedInput label="Roles" />}
                                renderValue={(selected) => (
                                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                                        {selected.map((value) => (
                                            <Chip key={value} label={value} size="small" />
                                        ))}
                                    </Box>
                                )}
                            >
                                <MenuItem value="admin">
                                    <Checkbox checked={formData.role.indexOf('admin') > -1} />
                                    <ListItemText primary="Administrator (All Access)" />
                                </MenuItem>
                                <MenuItem value="finance">
                                    <Checkbox checked={formData.role.indexOf('finance') > -1} />
                                    <ListItemText primary="Finance (Payments, Subscriptions)" />
                                </MenuItem>
                                <MenuItem value="employee">
                                    <Checkbox checked={formData.role.indexOf('employee') > -1} />
                                    <ListItemText primary="Employee (Tickets, Outages)" />
                                </MenuItem>
                                <MenuItem value="collector">
                                    <Checkbox checked={formData.role.indexOf('collector') > -1} />
                                    <ListItemText primary="Collector (Collect Payments)" />
                                </MenuItem>
                                <MenuItem value="cashier">
                                    <Checkbox checked={formData.role.indexOf('cashier') > -1} />
                                    <ListItemText primary="Office Cashier (Customers, Subscriptions, Collect Payments — no confirm)" />
                                </MenuItem>
                            </Select>
                        </FormControl>
                    </Box>
                </DialogContent>
                <DialogActions sx={{ p: 2, pt: 0 }}>
                    <Button onClick={handleCloseDialog} sx={{ color: 'text.secondary' }}>Cancel</Button>
                    <Button onClick={handleSaveUser} variant="contained" sx={{ borderRadius: '10px' }}>Save User</Button>
                </DialogActions>
            </Dialog>
        </Paper>
    );
};

export default UserManagement;
