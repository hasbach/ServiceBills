import React, { useEffect, useState } from 'react';
import { Alert, Box, CircularProgress, MenuItem, TextField } from '@mui/material';
import { apiService, useAppContext } from '../context/AppContext';
import NetworkMapView from './NetworkMapView';

// NetworkMapView's own canEdit gate is an exact-match `EDIT_ROLES.includes(userRole)`
// against a single role string (see NetworkMapView.js), not the comma-split
// "does the user hold any of these roles" check the rest of the app uses (see
// NetworkTreeView's canEditLinks, or App.js's hasRole). A user can legitimately
// hold a combined role string (e.g. "admin,finance" or "employee,collector"),
// so this picks the single highest-privilege role out of that same
// comma-separated `user.role` field -- not a second source of truth, just the
// existing field parsed the same way NetworkTreeView already parses it, then
// collapsed to the one token NetworkMapView's prop contract expects.
const ROLE_PRIORITY = ['admin', 'finance', 'employee', 'collector'];

function primaryRole(rawRole) {
    const roles = (rawRole || '').split(',').map((r) => r.trim().toLowerCase()).filter(Boolean);
    return ROLE_PRIORITY.find((r) => roles.includes(r)) || roles[0] || '';
}

// The map is scoped to one OLT (NetworkDevice with device_type 'vsol_olt');
// unlike NetworkTreeView, which renders every device at once, the map needs
// to know which one. Uses apiService.fetchNetworkMapOlts() (with fallback to
// fetchNetworkDevices for backwards compatibility) so field roles like
// employee and collector can load OLTs without being blocked by the admin-only
// /api/network-devices endpoint.
export default function NetworkMapPage() {
    const { user, setSnackbar } = useAppContext();
    const userRole = primaryRole(user?.role);

    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const [olts, setOlts] = useState([]);
    const [selectedOltId, setSelectedOltId] = useState('');

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const fetcher = apiService.fetchNetworkMapOlts || apiService.fetchNetworkDevices;
                const response = await fetcher();
                if (cancelled) return;
                const oltDevices = (response.data || []).filter((d) => d.device_type === 'vsol_olt');
                setOlts(oltDevices);
                // The common case -- exactly one OLT -- needs no interaction at
                // all: select it up front so the map renders immediately.
                if (oltDevices.length > 0) setSelectedOltId(oltDevices[0].id);
            } catch (err) {
                if (cancelled) return;
                setLoadError(true);
                setSnackbar({ open: true, severity: 'error',
                              message: 'Could not load network devices.' });
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [setSnackbar]);

    if (loading) {
        return (
            <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
                <CircularProgress />
            </Box>
        );
    }

    if (loadError) {
        return <Alert severity="error">Could not load network devices. Reload the page to try again.</Alert>;
    }

    if (olts.length === 0) {
        // Actionable rather than a blank map or an endless spinner: name the
        // exact fix (add an OLT device) and where to do it.
        return (
            <Alert severity="info">
                No OLT device is configured yet. Add one under "Network Devices"
                before the network map has anything to show.
            </Alert>
        );
    }

    return (
        <Box>
            {olts.length > 1 && (
                <TextField select size="small" label="OLT device" sx={{ mb: 2, minWidth: 260 }}
                    value={selectedOltId}
                    onChange={(e) => setSelectedOltId(e.target.value)}>
                    {olts.map((o) => (
                        <MenuItem key={o.id} value={o.id}>{o.name}</MenuItem>
                    ))}
                </TextField>
            )}
            {/* Remounts NetworkMapView on OLT switch so its own in-progress
                placement/edit state (add mode, pending parent, open dialog)
                never leaks from one OLT into another. */}
            <NetworkMapView key={selectedOltId} oltDeviceId={selectedOltId} userRole={userRole} />
        </Box>
    );
}
