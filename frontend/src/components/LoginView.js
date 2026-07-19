import React, { useState, useEffect } from 'react';
import { Box, Typography, TextField, Button, Paper, CircularProgress } from '@mui/material';
import { InstallMobile as InstallMobileIcon } from '@mui/icons-material';
import { Link, useNavigate } from 'react-router-dom';
import { useAppContext } from '../context/AppContext.js';

const LoginView = () => {
    const { login, setSnackbar } = useAppContext();
    const navigate = useNavigate();
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [loading, setLoading] = useState(false);
    const [installPromptEvent, setInstallPromptEvent] = useState(null);
    const [isIos, setIsIos] = useState(false);

    useEffect(() => {
        // Detect iOS
        const userAgent = window.navigator.userAgent.toLowerCase();
        if (/iphone|ipad|ipod/.test(userAgent) && !window.navigator.standalone) {
            setIsIos(true);
        }

        // Check if the event fired before React mounted
        if (window.deferredInstallPrompt) {
            setInstallPromptEvent(window.deferredInstallPrompt);
        }

        const handleBeforeInstallPrompt = (e) => {
            e.preventDefault();
            setInstallPromptEvent(e);
            window.deferredInstallPrompt = e;
        };
        window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
        return () => window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
    }, []);

    const handleInstallClick = async () => {
        if (!installPromptEvent) return;
        installPromptEvent.prompt();
        const { outcome } = await installPromptEvent.userChoice;
        if (outcome === 'accepted') {
            setInstallPromptEvent(null);
        }
    };

    const handleLogin = async (e) => {
        e.preventDefault();
        if (!username || !password) {
            setSnackbar({ open: true, message: 'Please enter username and password.', severity: 'warning' });
            return;
        }
        setLoading(true);
        try {
            await login({ username, password });
            navigate('/');   // clean URL after login (so a refresh doesn't land on /login)
        } catch (error) {
            setSnackbar({ open: true, message: error.response?.data?.msg || 'Login failed.', severity: 'error' });
        } finally {
            setLoading(false);
        }
    };

    return (
        <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
            <Paper component="form" onSubmit={handleLogin} sx={{ p: 4, width: '100%', maxWidth: '400px', borderRadius: '16px' }}>
                <Typography variant="h4" sx={{ mb: 3, textAlign: 'center', fontWeight: 'bold' }}>Login</Typography>
                <TextField
                    fullWidth
                    label="Username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    margin="normal"
                />
                <TextField
                    fullWidth
                    label="Password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    margin="normal"
                />
                <Button type="submit" variant="contained" fullWidth sx={{ mt: 2, py: 1.5 }} disabled={loading}>
                    {loading ? <CircularProgress size={24} /> : 'Login'}
                </Button>
                <Button component={Link} to="/register" fullWidth sx={{ mt: 2 }}>
                    Don't have an account? Register
                </Button>
                <Button component={Link} to="/forgot-password" fullWidth size="small" sx={{ mt: 1 }}>
                    Forgot password?
                </Button>
                {installPromptEvent && !isIos && (
                    <Button 
                        fullWidth 
                        variant="outlined" 
                        color="secondary" 
                        sx={{ mt: 2 }} 
                        startIcon={<InstallMobileIcon />}
                        onClick={handleInstallClick}
                    >
                        Install App
                    </Button>
                )}
                {!installPromptEvent && !isIos && (
                    <Typography variant="caption" sx={{ display: 'block', textAlign: 'center', mt: 2, color: 'text.secondary' }}>
                        To install this app, tap your browser's menu (⋮) and select <strong>"Install App"</strong> or <strong>"Add to Home screen"</strong>.
                    </Typography>
                )}
                {isIos && (
                    <Typography variant="caption" sx={{ display: 'block', textAlign: 'center', mt: 2, color: 'text.secondary' }}>
                        To install this app on iOS: tap the <strong>Share</strong> icon below, then select <strong>"Add to Home Screen"</strong>.
                    </Typography>
                )}
            </Paper>
        </Box>
    );
};

export default LoginView;
