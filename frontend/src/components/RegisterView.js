import React, { useState } from 'react';
import { Typography, TextField, Button, CircularProgress, Alert } from '@mui/material';
import { Link } from 'react-router-dom';
import { useAppContext } from '../context/AppContext.js';
import AuthShell from './AuthShell.js';

const RegisterView = () => {
    const { apiService } = useAppContext();
    const [form, setForm] = useState({ business_name: '', email: '', username: '', password: '' });
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [done, setDone] = useState(false);

    const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

    const handleRegister = async (e) => {
        e.preventDefault();
        setError('');
        setLoading(true);
        try {
            await apiService.register(form);
            setDone(true);
        } catch (err) {
            setError(err.response?.data?.msg || 'Registration failed.');
        } finally {
            setLoading(false);
        }
    };

    if (done) {
        return (
            <AuthShell>
                <Typography variant="h6" sx={{ mb: 1 }}>Almost there</Typography>
                <Typography sx={{ mb: 3 }}>
                    We've created <strong>{form.business_name || form.username}</strong>. Check
                    {form.email ? ` ${form.email}` : ' your email'} to verify your address, then log in.
                </Typography>
                <Button component={Link} to="/login" variant="contained" fullWidth>Go to login</Button>
            </AuthShell>
        );
    }

    return (
        <AuthShell>
            <Typography variant="h6" sx={{ mb: 2 }}>Create your servicesBills account</Typography>
            {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
            <form onSubmit={handleRegister}>
                <TextField fullWidth label="Business name" value={form.business_name}
                           onChange={set('business_name')} margin="normal" required />
                <TextField fullWidth type="email" label="Email" value={form.email}
                           onChange={set('email')} margin="normal" required />
                <TextField fullWidth label="Username" value={form.username}
                           onChange={set('username')} margin="normal" required />
                <TextField fullWidth type="password" label="Password" value={form.password}
                           onChange={set('password')} margin="normal" required />
                <Button type="submit" variant="contained" fullWidth sx={{ mt: 2, py: 1.3 }} disabled={loading}>
                    {loading ? <CircularProgress size={22} /> : 'Create account'}
                </Button>
            </form>
            <Button component={Link} to="/login" fullWidth sx={{ mt: 2 }}>
                Already have an account? Log in
            </Button>
        </AuthShell>
    );
};

export default RegisterView;
