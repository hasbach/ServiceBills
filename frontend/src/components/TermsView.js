import React from 'react';
import { Box, Container, Typography, Link as MuiLink, Divider } from '@mui/material';
import { Link } from 'react-router-dom';

// Public Terms of Service at /terms (also referenced as the Terms of Service
// URL of the platform Meta app used for WhatsApp Embedded Signup).
const UPDATED = '20 October 2026';
const CONTACT = 'support@salloumservices.com';

const Section = ({ title, children }) => (
    <Box component="section" sx={{ mb: 3 }}>
        <Typography variant="h6" component="h2" sx={{ fontWeight: 700, mb: 1 }}>{title}</Typography>
        {children}
    </Box>
);

const P = ({ children }) => (
    <Typography variant="body1" sx={{ mb: 1.25, lineHeight: 1.7 }}>{children}</Typography>
);

const TermsView = () => (
    <Box sx={{ bgcolor: 'background.default', minHeight: '100vh' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', px: { xs: 2, md: 6 }, py: 2 }}>
            <Typography component={Link} to="/" variant="h6"
                sx={{ fontWeight: 800, color: 'primary.main', textDecoration: 'none' }}>
                servicesBills
            </Typography>
        </Box>
        <Container maxWidth="md" sx={{ pb: 8, px: { xs: 2, sm: 3 } }}>
            <Typography variant="h4" component="h1" sx={{ fontWeight: 800, mt: 2, mb: 0.5 }}>Terms of Service</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>Last updated: {UPDATED}</Typography>

            <Section title="1. Who we are">
                <P>
                    servicesBills ("the Service") is a billing, customer-management and network-monitoring platform
                    for internet service providers and resellers, operated by Beta consulting Agency ("we", "us").
                    These terms apply to everyone who creates an account or uses the Service ("you").
                </P>
            </Section>

            <Section title="2. Your account">
                <P>
                    You are responsible for the accuracy of the information you enter, for keeping your login details
                    secret, and for everything done under your account, including by users you invite. Tell us
                    promptly at {CONTACT} if you believe your account has been accessed without permission.
                </P>
            </Section>

            <Section title="3. Acceptable use">
                <P>
                    You may use the Service only for lawful business purposes. You must not send spam or unsolicited
                    messages, upload content you have no right to use, attempt to access other customers' data, or
                    interfere with the security or operation of the Service.
                </P>
            </Section>

            <Section title="4. WhatsApp messaging">
                <P>
                    The Service can send and receive WhatsApp messages on your behalf through the WhatsApp Business
                    Platform provided by Meta. When you connect a WhatsApp number, you authorize us to access that
                    WhatsApp Business account to send messages, manage message templates and receive incoming
                    messages and status updates for you. You can disconnect at any time from Settings, or remove our
                    access in Meta Business Settings.
                </P>
                <P>
                    You remain the sender of your messages. You must have your customers' consent to message them,
                    and you must follow the{' '}
                    <MuiLink href="https://www.whatsapp.com/legal/business-policy" target="_blank" rel="noopener noreferrer">WhatsApp Business Policy</MuiLink>,{' '}
                    <MuiLink href="https://www.whatsapp.com/legal/commerce-policy" target="_blank" rel="noopener noreferrer">WhatsApp Commerce Policy</MuiLink>{' '}
                    and Meta's terms for the WhatsApp Business Platform. Fees charged by Meta for WhatsApp
                    conversations are billed by Meta to your own WhatsApp Business account.
                </P>
            </Section>

            <Section title="5. Your data">
                <P>
                    You own the data you put into the Service, including customer records and message history. We
                    process it only to provide the Service to you, as described in our privacy policy. When you close
                    your account you can ask us at {CONTACT} to export or delete your data.
                </P>
            </Section>

            <Section title="6. Plans and payment">
                <P>
                    Paid plans are billed as shown on our pricing page or agreed with us in writing. If a payment is
                    not made, we may limit paid features after notifying you. Prices may change with at least 30 days'
                    notice.
                </P>
            </Section>

            <Section title="7. Availability and changes">
                <P>
                    We work to keep the Service available and secure but do not guarantee uninterrupted operation.
                    Third-party services the Service depends on, such as Meta's WhatsApp platform or your upstream
                    providers, may change or become unavailable. We may update the Service and these terms; material
                    changes will be announced in the Service or by email.
                </P>
            </Section>

            <Section title="8. Liability">
                <P>
                    The Service is provided "as is". To the extent permitted by law, we are not liable for indirect or
                    consequential losses, lost profits or lost data, and our total liability for any claim is limited
                    to the amount you paid us for the Service in the three months before the claim.
                </P>
            </Section>

            <Section title="9. Ending the agreement">
                <P>
                    You may stop using the Service at any time. We may suspend or close an account that breaches these
                    terms, after notice where reasonably possible.
                </P>
            </Section>

            <Divider sx={{ my: 3 }} />
            <Typography variant="body2" color="text.secondary">
                Questions about these terms: <MuiLink href={`mailto:${CONTACT}`}>{CONTACT}</MuiLink>
            </Typography>
        </Container>
    </Box>
);

export default TermsView;
