import React, { useState, useEffect } from 'react';
import {
    Dialog, DialogTitle, DialogContent, DialogActions, Button, Box, Typography,
    Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Checkbox,
    Chip, CircularProgress, Alert, Paper,
} from '@mui/material';
import { apiService, useAppContext } from '../context/AppContext';

const OnuLabelMatcherDialog = ({ device, onClose, onApplied }) => {
    const { setSnackbar } = useAppContext();
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [proposals, setProposals] = useState([]);
    const [unmatchedOnus, setUnmatchedOnus] = useState([]);
    const [unmatchedCustomers, setUnmatchedCustomers] = useState([]);
    const [accepted, setAccepted] = useState({});
    const [applying, setApplying] = useState(false);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const res = await apiService.fetchOnuLabelMatches(device.id);
                if (cancelled) return;
                if (!res.data.ok) {
                    setError(res.data.message);
                } else {
                    setProposals(res.data.proposals);
                    setUnmatchedOnus(res.data.unmatched_onus);
                    setUnmatchedCustomers(res.data.unmatched_customers);
                    // Pre-tick every proposal -- the user reviews and unticks
                    // the ones they disagree with, rather than ticking 70.
                    const initial = {};
                    res.data.proposals.forEach((p) => { initial[p.onu.mac_address] = true; });
                    setAccepted(initial);
                }
            } catch (e) {
                if (!cancelled) setError('Failed to load label matches');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [device.id]);

    const acceptedCount = proposals.filter((p) => accepted[p.onu.mac_address]).length;

    const apply = async () => {
        setApplying(true);
        try {
            const links = proposals
                .filter((p) => accepted[p.onu.mac_address])
                .map((p) => ({ customer_id: p.customer.id, mac_address: p.onu.mac_address }));
            const res = await apiService.applyOnuLabelMatches(device.id, links);
            setSnackbar({ open: true, severity: 'success',
                message: `Linked ${res.data.applied} customer(s) to their ONU.` });
            onApplied();
        } catch (e) {
            const message = e?.response?.data?.error || 'Failed to apply links';
            setSnackbar({ open: true, severity: 'error', message });
        } finally {
            setApplying(false);
        }
    };

    const handleDialogClose = () => {
        if (applying) return;
        onClose();
    };

    return (
        <Dialog open onClose={handleDialogClose} disableEscapeKeyDown={applying} fullWidth maxWidth="md">
            <DialogTitle>Match ONU labels to customers — {device.name}</DialogTitle>
            <DialogContent dividers>
                {loading && <Box sx={{ p: 4, textAlign: 'center' }}><CircularProgress /></Box>}
                {error && <Alert severity="error">{error}</Alert>}

                {!loading && !error && (
                    <>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                            These are suggestions from the labels already typed into the OLT.
                            Nothing is saved until you press Apply. Untick anything that looks wrong.
                        </Typography>

                        {proposals.length === 0 ? (
                            <Alert severity="info">
                                No new matches to propose — every labelled ONU is either already
                                linked or has no similar customer name.
                            </Alert>
                        ) : (
                            <TableContainer component={Paper} variant="outlined">
                                <Table size="small">
                                    <TableHead>
                                        <TableRow>
                                            <TableCell padding="checkbox" />
                                            <TableCell>OLT label</TableCell>
                                            <TableCell>Customer</TableCell>
                                            <TableCell>ONU</TableCell>
                                            <TableCell align="right">Confidence</TableCell>
                                        </TableRow>
                                    </TableHead>
                                    <TableBody>
                                        {proposals.map((p) => (
                                            <TableRow key={p.onu.mac_address} hover>
                                                <TableCell padding="checkbox">
                                                    <Checkbox
                                                        checked={!!accepted[p.onu.mac_address]}
                                                        onChange={(e) => setAccepted((prev) => ({
                                                            ...prev, [p.onu.mac_address]: e.target.checked,
                                                        }))} />
                                                </TableCell>
                                                <TableCell>{p.onu.description}</TableCell>
                                                <TableCell>{p.customer.name}</TableCell>
                                                <TableCell>
                                                    <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                                                        {p.onu.onu_id} · {p.onu.mac_address}
                                                    </Typography>
                                                </TableCell>
                                                <TableCell align="right">
                                                    <Chip size="small"
                                                        color={p.confidence >= 0.99 ? 'success' : 'warning'}
                                                        label={`${Math.round(p.confidence * 100)}%`} />
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </TableContainer>
                        )}

                        <Typography variant="subtitle2" sx={{ mt: 3 }}>
                            Unmatched ONUs ({unmatchedOnus.length})
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                            {unmatchedOnus.length === 0 ? 'None.' : unmatchedOnus
                                .map((o) => o.description || o.onu_id).join(', ')}
                        </Typography>

                        <Typography variant="subtitle2" sx={{ mt: 2 }}>
                            Customers with no ONU ({unmatchedCustomers.length})
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                            {unmatchedCustomers.length === 0 ? 'None.' : unmatchedCustomers
                                .map((c) => c.name).join(', ')}
                        </Typography>
                    </>
                )}
            </DialogContent>
            <DialogActions>
                <Button onClick={onClose} disabled={applying}>Cancel</Button>
                <Button variant="contained" onClick={apply}
                    disabled={applying || acceptedCount === 0}>
                    {applying ? 'Applying…' : `Apply ${acceptedCount} link(s)`}
                </Button>
            </DialogActions>
        </Dialog>
    );
};

export default OnuLabelMatcherDialog;
