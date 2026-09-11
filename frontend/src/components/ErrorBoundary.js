import React from 'react';
import { Box, Typography, Button, Paper } from '@mui/material';

class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, error: null };
    }

    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }

    componentDidCatch(error, errorInfo) {
        console.error("ErrorBoundary caught an error:", error, errorInfo);
    }

    handleReload = () => {
        window.location.reload();
    };

    handleReset = () => {
        this.setState({ hasError: false, error: null });
    };

    render() {
        if (this.state.hasError) {
            return (
                <Box
                    sx={{
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        justifyContent: 'center',
                        minHeight: '60vh',
                        p: 3,
                    }}
                >
                    <Paper
                        elevation={3}
                        sx={{
                            p: 4,
                            maxWidth: 550,
                            width: '100%',
                            textAlign: 'center',
                            borderRadius: 2,
                        }}
                    >
                        <Typography variant="h5" color="error" gutterBottom sx={{ fontWeight: 600 }}>
                            Something went wrong
                        </Typography>
                        <Typography variant="body1" color="text.secondary" sx={{ mb: 3 }}>
                            An unexpected error occurred while rendering this page. You can try refreshing or returning to the dashboard.
                        </Typography>
                        {this.state.error?.message && (
                            <Typography
                                variant="caption"
                                display="block"
                                sx={{
                                    mb: 3,
                                    p: 1.5,
                                    bgcolor: 'action.hover',
                                    borderRadius: 1,
                                    fontFamily: 'monospace',
                                    wordBreak: 'break-word',
                                    textAlign: 'left',
                                }}
                            >
                                {this.state.error.message}
                            </Typography>
                        )}
                        <Box sx={{ display: 'flex', gap: 2, justifyContent: 'center' }}>
                            <Button variant="outlined" onClick={this.handleReset}>
                                Try Again
                            </Button>
                            <Button variant="contained" color="primary" onClick={this.handleReload}>
                                Reload Page
                            </Button>
                        </Box>
                    </Paper>
                </Box>
            );
        }

        return this.props.children;
    }
}

export default ErrorBoundary;
