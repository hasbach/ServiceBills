import React, { useState, useEffect } from 'react';
import { Typography, Button, CircularProgress, Box } from '@mui/material';
import { Link, useSearchParams } from 'react-router-dom';
import { useAppContext } from '../context/AppContext.js';
import AuthShell from './AuthShell.js';

const VerifyEmailView = () => {
    const { apiService } = useAppContext();
    const [params] = useSearchParams();
    const [state, setState] = useState('loading'); // loading | ok | error

    useEffect(() => {
        const token = params.get('token');
        if (!token) { setState('error'); return; }
        apiService.verifyEmail(token).then(() => setState('ok')).catch(() => setState('error'));
    }, [params, apiService]);

    return (
        <AuthShell>
            {state === 'loading' && <Box sx={{ textAlign: 'center' }}><CircularProgress /></Box>}
            {state === 'ok' && (
                <>
                    <Typography sx={{ mb: 3 }}>Your email is verified. You're all set.</Typography>
                    <Button component={Link} to="/login" variant="contained" fullWidth>Go to login</Button>
                </>
            )}
            {state === 'error' && (
                <>
                    <Typography color="error" sx={{ mb: 3 }}>This verification link is invalid or has expired.</Typography>
                    <Button component={Link} to="/login" fullWidth>Back to login</Button>
                </>
            )}
        </AuthShell>
    );
};

export default VerifyEmailView;
