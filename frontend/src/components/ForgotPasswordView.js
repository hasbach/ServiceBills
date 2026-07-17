import React, { useState } from 'react';
import { Typography, TextField, Button, CircularProgress } from '@mui/material';
import { Link } from 'react-router-dom';
import { useAppContext } from '../context/AppContext.js';
import AuthShell from './AuthShell.js';

const ForgotPasswordView = () => {
    const { apiService } = useAppContext();
    const [email, setEmail] = useState('');
    const [loading, setLoading] = useState(false);
    const [sent, setSent] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setLoading(true);
        try {
            await apiService.forgotPassword(email);
        } finally {
            setLoading(false);
            setSent(true); // Always show the same confirmation (no account enumeration).
        }
    };

    if (sent) {
        return (
            <AuthShell>
                <Typography sx={{ mb: 3 }}>
                    If an account exists for that email, a password-reset link is on its way.
                </Typography>
                <Button component={Link} to="/login" fullWidth>Back to login</Button>
            </AuthShell>
        );
    }

    return (
        <AuthShell>
            <Typography variant="h6" sx={{ mb: 2 }}>Reset your password</Typography>
            <form onSubmit={handleSubmit}>
                <TextField fullWidth type="email" label="Email" value={email}
                           onChange={(e) => setEmail(e.target.value)} margin="normal" required />
                <Button type="submit" variant="contained" fullWidth sx={{ mt: 2, py: 1.3 }} disabled={loading}>
                    {loading ? <CircularProgress size={22} /> : 'Send reset link'}
                </Button>
            </form>
            <Button component={Link} to="/login" fullWidth sx={{ mt: 2 }}>Back to login</Button>
        </AuthShell>
    );
};

export default ForgotPasswordView;
