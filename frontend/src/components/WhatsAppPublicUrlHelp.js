import React from 'react';
import { Dialog, DialogTitle, DialogContent, DialogActions, Button, Typography } from '@mui/material';

const STEPS = [
    'Get a domain name, or a free dynamic-DNS name (for example from DuckDNS or No-IP) that points to your internet connection.',
    "Log in to your router and forward TCP port 443 to this PC.",
    'Install an HTTPS reverse proxy such as Caddy on this PC and point it at ServiceBills on port 8000. Caddy gets a free HTTPS certificate for you automatically.',
    'Paste your https address (for example https://billing.example.com) into the Public URL field and save.',
    "Copy the Webhook URL and Verify Token shown on this page into Meta's WhatsApp configuration, then click Verify and Save.",
];

const WhatsAppPublicUrlHelp = ({ open, onClose }) => (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
        <DialogTitle>How do I make this PC reachable from the internet?</DialogTitle>
        <DialogContent dividers>
            <Typography variant="body2" sx={{ mb: 2 }}>
                WhatsApp (Meta) needs to reach this PC over the internet to deliver customer replies and message status updates. Follow these steps once:
            </Typography>
            <ol style={{ paddingLeft: 20, margin: 0 }}>
                {STEPS.map((s, i) => (
                    <li key={i} style={{ marginBottom: 8 }}><Typography variant="body2">{s}</Typography></li>
                ))}
            </ol>
        </DialogContent>
        <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
);

export default WhatsAppPublicUrlHelp;
