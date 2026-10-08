import React from 'react';
import { Box, Typography, Link, Stack } from '@mui/material';
import { Phone as PhoneIcon, WhatsApp as WhatsAppIcon, Email as EmailIcon } from '@mui/icons-material';

// Who to reach to subscribe to / renew Pro.
export const SALES_PHONE_DISPLAY = '+961 79 170 372';
export const SALES_PHONE_E164 = '+96179170372';
export const SALES_EMAIL = 'support@salloumservices.com';

const SalesContact = ({ title = 'To subscribe or renew Pro, contact us:', align = 'left' }) => (
    <Box sx={{ textAlign: align }}>
        {title && <Typography variant="body2" sx={{ fontWeight: 600, mb: 1 }}>{title}</Typography>}
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 1, sm: 3 }}
               justifyContent={align === 'center' ? 'center' : 'flex-start'} flexWrap="wrap">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, justifyContent: align === 'center' ? 'center' : 'flex-start' }}>
                <PhoneIcon fontSize="small" color="primary" />
                <Link href={`tel:${SALES_PHONE_E164}`} underline="hover">{SALES_PHONE_DISPLAY}</Link>
                <Link href={`https://wa.me/${SALES_PHONE_E164.slice(1)}`} target="_blank" rel="noopener noreferrer"
                      aria-label="WhatsApp" sx={{ display: 'inline-flex', ml: 0.5 }}>
                    <WhatsAppIcon fontSize="small" sx={{ color: '#25D366' }} />
                </Link>
            </Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75, justifyContent: align === 'center' ? 'center' : 'flex-start' }}>
                <EmailIcon fontSize="small" color="primary" />
                <Link href={`mailto:${SALES_EMAIL}`} underline="hover">{SALES_EMAIL}</Link>
            </Box>
        </Stack>
    </Box>
);

export default SalesContact;
