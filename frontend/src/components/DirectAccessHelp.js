import React, { useState } from 'react';
import {
    Accordion, AccordionSummary, AccordionDetails, Box, Typography, IconButton,
    Tooltip, Alert, Table, TableHead, TableRow, TableCell, TableBody,
} from '@mui/material';
import {
    ExpandMore as ExpandMoreIcon,
    ContentCopy as CopyIcon,
    Check as CheckIcon,
    Public as PublicIcon,
} from '@mui/icons-material';

// Outbound IP ranges of this app's hosting (Render), as listed in the Render
// dashboard: service -> Connect -> Outbound. Devices only need to accept
// traffic from these. Render can change them (rarely, with notice) -- if a
// direct-mode device suddenly shows "unreachable" for every tenant, re-check
// the dashboard and update this list.
export const APP_OUTBOUND_IPS = ['74.220.48.0/24', '74.220.56.0/24'];

function CodeBlock({ children }) {
    const [copied, setCopied] = useState(false);
    const copy = () => {
        try {
            navigator.clipboard.writeText(children);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch (e) { /* clipboard unavailable -- the text is still selectable */ }
    };
    return (
        <Box sx={{ position: 'relative', my: 1 }}>
            <Box component="pre" sx={{
                m: 0, p: 1.5, pr: 5, bgcolor: '#0f172a', color: '#e2e8f0', borderRadius: '8px',
                fontSize: '0.8rem', fontFamily: 'ui-monospace, Consolas, monospace',
                whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            }}>{children}</Box>
            <Tooltip title={copied ? 'Copied' : 'Copy'}>
                <IconButton size="small" onClick={copy} aria-label="Copy command"
                    sx={{ position: 'absolute', top: 4, right: 4, color: '#94a3b8' }}>
                    {copied ? <CheckIcon fontSize="small" /> : <CopyIcon fontSize="small" />}
                </IconButton>
            </Tooltip>
        </Box>
    );
}

function Step({ n, title, children }) {
    return (
        <Box sx={{ mb: 2.5 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>{n}. {title}</Typography>
            {children}
        </Box>
    );
}

const addressList = APP_OUTBOUND_IPS
    .map(ip => `/ip firewall address-list add list=app-outbound address=${ip} comment="Billing app servers"`)
    .join('\n');

// Setup guide for 'direct' network access from the cloud: port-forward each
// device through the tenant's public IP, and only accept that traffic from
// this app's outbound IPs. Written with placeholders only -- no tenant's real
// addresses -- since every tenant sees the same text.
export default function DirectAccessHelp() {
    return (
        <Accordion disableGutters elevation={0} sx={{ mb: 3, border: '1px solid #e0e0e0', borderRadius: '12px !important', '&:before': { display: 'none' } }}>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <PublicIcon color="primary" fontSize="small" />
                    <Typography sx={{ fontWeight: 600 }}>Setup guide: reach your devices from the cloud without the on-prem agent</Typography>
                </Box>
            </AccordionSummary>
            <AccordionDetails>
                <Typography variant="body2" sx={{ mb: 2 }}>
                    If your network has a public IP, the app can reach your devices directly through port forwards on your
                    router instead of running the on-prem agent. Set <b>Settings → Network Device Access</b> to <b>Direct</b>,
                    forward one port per device, and accept that traffic <b>only</b> from the app's servers.
                </Typography>

                <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>Which port each device needs</Typography>
                <Table size="small" sx={{ mb: 2.5, maxWidth: 640 }}>
                    <TableHead>
                        <TableRow>
                            <TableCell sx={{ fontWeight: 700 }}>Device</TableCell>
                            <TableCell sx={{ fontWeight: 700 }}>Protocol</TableCell>
                            <TableCell sx={{ fontWeight: 700 }}>Port on the device</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        <TableRow><TableCell>VSOL OLT (SNMP)</TableCell><TableCell>UDP</TableCell><TableCell>161</TableCell></TableRow>
                        <TableRow><TableCell>MikroTik router (API-SSL, recommended)</TableCell><TableCell>TCP</TableCell><TableCell>8729</TableCell></TableRow>
                        <TableRow><TableCell>MikroTik router (API, unencrypted)</TableCell><TableCell>TCP</TableCell><TableCell>8728</TableCell></TableRow>
                    </TableBody>
                </Table>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
                    The OLT's web page (often port 8080) is not used by the app. Don't forward it for this.
                </Typography>

                <Typography variant="body2" sx={{ mb: 2 }}>
                    Commands below are for a MikroTik router holding your public IP (<b>Terminal</b> in WinBox/WebFig). Replace
                    everything in <code>&lt;angle brackets&gt;</code>. For another router brand, create the same port forward and
                    source-IP restriction in its own firewall settings.
                </Typography>

                <Step n={1} title="Add the app's servers to an address list">
                    <Typography variant="body2">These are the only addresses the app connects from:</Typography>
                    <CodeBlock>{addressList}</CodeBlock>
                </Step>

                <Step n={2} title="Forward a port on your public IP to the device — only from that list">
                    <Typography variant="body2">
                        Pick an unused port for each device (a non-standard one, e.g. above 10000, gets less random scanning).
                        For the OLT (SNMP is <b>UDP</b>):
                    </Typography>
                    <CodeBlock>{`/ip firewall nat add chain=dstnat dst-address=<your-public-ip> protocol=udp dst-port=<your-desired-port> src-address-list=app-outbound action=dst-nat to-addresses=<your-device-ip> to-ports=161 comment="OLT SNMP for billing app"`}</CodeBlock>
                    <Typography variant="body2">For a MikroTik router behind this one (API-SSL is <b>TCP</b>):</Typography>
                    <CodeBlock>{`/ip firewall nat add chain=dstnat dst-address=<your-public-ip> protocol=tcp dst-port=<your-desired-port> src-address-list=app-outbound action=dst-nat to-addresses=<your-device-ip> to-ports=8729 comment="Router API for billing app"`}</CodeBlock>
                    <Typography variant="body2" color="text.secondary">
                        If your public IP changes (dynamic), use <code>in-interface=&lt;your-wan-interface&gt;</code> instead
                        of <code>dst-address=&lt;your-public-ip&gt;</code>.
                    </Typography>
                </Step>

                <Step n={3} title="Already added a forward without the restriction? Restrict it">
                    <Typography variant="body2">Find the rule's number, then add the source list to it:</Typography>
                    <CodeBlock>{`/ip firewall nat print where dst-port=<your-desired-port>
/ip firewall nat set <rule-number> src-address-list=app-outbound`}</CodeBlock>
                </Step>

                <Step n={4} title="If the device is the router that holds the public IP itself">
                    <Typography variant="body2">
                        No forward is needed — allow the API only from the app's servers:
                    </Typography>
                    <CodeBlock>{`/ip service set api-ssl disabled=no address=${APP_OUTBOUND_IPS.join(',')}`}</CodeBlock>
                    <Typography variant="body2" color="text.secondary">
                        If you also connect to the API from your office, append your own addresses to that list, separated by
                        commas. API-SSL needs a certificate assigned to the service (System → Certificates) — or use the plain
                        <code> api</code> service (8728) with the same <code>address=</code> restriction.
                    </Typography>
                </Step>

                <Step n={5} title="Enter the device in this page">
                    <Typography variant="body2" component="div">
                        Add or edit the device with <b>Host</b> = <code>&lt;your-public-ip&gt;</code> and <b>Port</b> ={' '}
                        <code>&lt;your-desired-port&gt;</code> (the outside port from step 2, not the device's own port). For an
                        OLT, the <b>Password</b> field is its SNMP community string. Then use <b>Test Connection</b> / <b>Check Now</b>.
                    </Typography>
                </Step>

                <Step n={6} title="Check the restriction works">
                    <CodeBlock>{`/ip firewall nat print stats where dst-port=<your-desired-port>`}</CodeBlock>
                    <Typography variant="body2" color="text.secondary">
                        The packet counter should only increase when the app checks the device.
                    </Typography>
                </Step>

                <Alert severity="warning" sx={{ mt: 1 }}>
                    <b>Security:</b> never forward these ports without the address-list restriction. SNMP sends its community
                    string unencrypted, so use a long random community (not <code>public</code>) set to <b>read-only</b>, and
                    prefer API-SSL (8729) over plain API (8728) for routers. If your devices only have private addresses, or you'd
                    rather not open any port, use the on-prem agent instead (Settings → Network Device Access → Via on-prem agent).
                </Alert>
            </AccordionDetails>
        </Accordion>
    );
}
