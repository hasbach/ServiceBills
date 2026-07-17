import React, { useState } from 'react';
import { Typography, TextField, Button, CircularProgress } from '@mui/material';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import { useAppContext } from '../context/AppContext.js';
import AuthShell from './AuthShell.js';

const ResetPasswordView = () => {
    const { apiService, setSnackbar } = useAppContext();
    const [params] = useSearchParams();
    const navigate = useNavigate();
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        const token = params.get('token');
        if (!token) {
            setSnackbar({ open: true, message: 'Missing reset token.', severity: 'error' });
            return;
        }
        setLoading(true);
        try {
            await apiService.resetPassword(token, password);
            setSnackbar({ open: true, message: 'Password updated. Please log in.', severity: 'success' });
            navigate('/login');
        } catch (err) {
            setSnackbar({ open: true, message: err.response?.data?.msg || 'Reset failed.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    return (
        <AuthShell>
            <Typography variant="h6" sx={{ mb: 2 }}>Choose a new password</Typography>
            <form onSubmit={handleSubmit}>
                <TextField fullWidth type="password" label="New password" value={password}
                           onChange={(e) => setPassword(e.target.value)} margin="normal" required />
                <Button type="submit" variant="contained" fullWidth sx={{ mt: 2, py: 1.3 }} disabled={loading}>
                    {loading ? <CircularProgress size={22} /> : 'Update password'}
                </Button>
            </form>
            <Button component={Link} to="/login" fullWidth sx={{ mt: 2 }}>Back to login</Button>
        </AuthShell>
    );
};

export default ResetPasswordView;
