import React from 'react';
import { Box, Paper, Typography } from '@mui/material';

// Centered card used by all public/auth screens, on the brand background.
const AuthShell = ({ children }) => (
    <Box sx={{
        display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center',
        minHeight: '100vh', p: 2, bgcolor: 'background.default',
    }}>
        <Box component="img" src="/serviceBillsLogo.png" alt="servicesBills" sx={{ width: 64, height: 64, mb: 1 }} />
        <Typography variant="h5" sx={{ fontWeight: 800, color: 'primary.main', mb: 2 }}>
            servicesBills
        </Typography>
        <Paper sx={{ p: 4, width: '100%', maxWidth: 420, borderRadius: 3 }} elevation={2}>
            {children}
        </Paper>
    </Box>
);

export default AuthShell;
