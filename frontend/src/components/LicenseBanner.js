import React, { useState, useEffect } from 'react';
import { Alert, Button } from '@mui/material';
import { useAppContext } from '../context/AppContext.js';
import { reasonText, expiryWarnings } from '../utils/licenseStatus';

// On-prem only: persistent red banner when read-only, dismissible amber
// banners for licenses/modules that expire soon.
const LicenseBanner = ({ onEnterLicense }) => {
    const { systemInfo, readOnly, apiService, user } = useAppContext();
    const [dismissed, setDismissed] = useState({});
    const [detail, setDetail] = useState(null);
    const license = systemInfo?.license;
    const isAdmin = (user?.role || '').split(',').map(r => r.trim()).includes('admin');

    // Expiry warnings come from the admin-only /api/license (system info
    // carries only the summary state).
    useEffect(() => {
        if (!isAdmin) return undefined;
        let cancelled = false;
        apiService.getLicense()
            .then(r => { if (!cancelled) setDetail(r.data); })
            .catch(() => {});
        return () => { cancelled = true; };
    }, [isAdmin, apiService, systemInfo]);

    if (readOnly) {
        return (
            <Alert severity="error" variant="filled" square
                action={<Button color="inherit" size="small" onClick={onEnterLicense}>Enter license</Button>}>
                ServiceBills is in view-only mode. {reasonText(license?.reason)}
            </Alert>
        );
    }

    const warnings = expiryWarnings(detail).filter(w => !dismissed[w.label]);
    return (
        <>
            {warnings.map(w => (
                <Alert key={w.label} severity="warning" square onClose={() => setDismissed(d => ({ ...d, [w.label]: true }))}>
                    {w.label} expires {w.daysLeft === 0 ? 'today' : `in ${w.daysLeft} day${w.daysLeft === 1 ? '' : 's'}`} — renew to keep using it.
                </Alert>
            ))}
        </>
    );
};

export default LicenseBanner;
