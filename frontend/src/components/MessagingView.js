import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Paper, Grid, TextField, Button, MenuItem,
    FormControl, InputLabel, Select, CircularProgress, Alert, Switch, FormControlLabel,
    Tabs, Tab, Chip, Divider, Stack
} from '@mui/material';
import {
    Send as SendIcon, Sync as SyncIcon, UploadFile as UploadFileIcon, WhatsApp as WhatsAppIcon,
    Assessment as AssessmentIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext';
import InboxView from './inbox/InboxView';
import Composer from './inbox/Composer';
import InboxNotificationsControl from './inbox/InboxNotificationsControl';
import CampaignsView from './CampaignsView';

const MessagingView = ({ openConversationId = null }) => {
    const { apiService, setSnackbar } = useAppContext();
    const [loading, setLoading] = useState(false);
    const [activeTab, setActiveTab] = useState(0);
    useEffect(() => { if (openConversationId) setActiveTab(0); }, [openConversationId]);

    // Existing customer bulk messaging states
    const [audience, setAudience] = useState('all');
    const [eventType, setEventType] = useState('outage');
    const [message, setMessage] = useState('an outage occured from the isp , will be repaired soon');
    const [location, setLocation] = useState('');
    const [estimatedTime, setEstimatedTime] = useState('');
    const [targetSector, setTargetSector] = useState('');
    const [excludeResellerCustomers, setExcludeResellerCustomers] = useState(false);
    const [sectors, setSectors] = useState([]);

    // Meta Marketing / Custom List states
    const [metaTemplates, setMetaTemplates] = useState([]);
    const [selectedTemplate, setSelectedTemplate] = useState('');
    const [syncing, setSyncing] = useState(false);
    const [customPhonesText, setCustomPhonesText] = useState('');
    const [customVariables, setCustomVariables] = useState('');
    const [headerUrl, setHeaderUrl] = useState('');
    const [openCampaignId, setOpenCampaignId] = useState(null);
    const clearOpenCampaign = useCallback(() => setOpenCampaignId(null), []);

    const fetchSectorsData = useCallback(async () => {
        try {
            const response = await apiService.fetchSectors();
            setSectors(response.data);
        } catch (error) {
            console.error("Failed to fetch sectors:", error);
        }
    }, [apiService]);

    // Mount-time, passive load: reads the local template cache (no live Meta
    // call, no plan/mode gating), so it's safe for every tenant regardless of
    // plan or sending mode. Failures are logged only -- the Marketing Campaign
    // tab's dropdown just shows "No templates loaded" until the user hits Sync.
    const handleFetchTemplates = useCallback(async () => {
        try {
            const res = await apiService.fetchWhatsAppTemplates();
            const loaded = (res.data.templates || []).filter(t => t.status === 'APPROVED');
            setMetaTemplates(loaded);
            if (loaded.length > 0 && !selectedTemplate) {
                setSelectedTemplate(loaded[0].name);
            }
        } catch (error) {
            console.error("Failed to load WhatsApp templates:", error);
        }
    }, [apiService, selectedTemplate]);

    // Explicit user action (Sync button): a live Meta API call, gated on
    // mode == 'api' + the whatsapp_api plan feature -- only triggered by a click.
    const handleSyncTemplates = useCallback(async () => {
        setSyncing(true);
        try {
            const res = await apiService.syncWhatsAppTemplates();
            const loaded = (res.data.templates || []).filter(t => t.status === 'APPROVED');
            setMetaTemplates(loaded);
            if (loaded.length > 0 && !selectedTemplate) {
                setSelectedTemplate(loaded[0].name);
            }
            setSnackbar({ open: true, message: `Synchronized ${loaded.length} approved templates from Meta.`, severity: 'success' });
        } catch (error) {
            console.error("Failed to sync Meta templates:", error);
            setSnackbar({ open: true, message: error.response?.data?.error || 'Failed to synchronize templates from Meta.', severity: 'error' });
        } finally {
            setSyncing(false);
        }
    }, [apiService, setSnackbar, selectedTemplate]);

    useEffect(() => {
        fetchSectorsData();
        handleFetchTemplates();
    }, [fetchSectorsData]);

    const handleEventTypeChange = (e) => {
        const newType = e.target.value;
        setEventType(newType);
        if (newType === 'outage') {
            setMessage('an outage occured from the isp , will be repaired soon');
        } else if (newType === 'feature' || newType === 'offer') {
            setMessage('');
        }
    };

    const handleFileUpload = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (evt) => {
            const content = evt.target.result;
            const lines = content.split(/\r?\n|,/);
            const extracted = lines
                .map(line => line.replace(/[^0-9+]/g, '').trim())
                .filter(num => num.length >= 7);
            const uniquePhones = [...new Set(extracted)];
            setCustomPhonesText(uniquePhones.join('\n'));
            setSnackbar({ open: true, message: `Loaded ${uniquePhones.length} mobile numbers from ${file.name}.`, severity: 'success' });
        };
        reader.readAsText(file);
    };

    const handleSend = async () => {
        setLoading(true);
        try {
            let payload = {};

            if (activeTab === 1) {
                const variables = {};
                if (eventType === 'outage' || eventType === 'feature' || eventType === 'offer') {
                    variables.message = message;
                } else if (eventType === 'maintenance') {
                    variables.location = location;
                    variables.estimated_time = estimatedTime;
                }

                payload = {
                    audience,
                    event_type: eventType,
                    variables,
                    exclude_reseller_customers: excludeResellerCustomers,
                    sector: targetSector
                };
            } else {
                const phoneList = customPhonesText.split(/\r?\n|,/)
                    .map(p => p.trim())
                    .filter(p => p.length >= 7);

                if (phoneList.length === 0) {
                    setSnackbar({ open: true, message: 'Please enter or upload at least one valid phone number.', severity: 'warning' });
                    setLoading(false);
                    return;
                }
                if (!selectedTemplate) {
                    setSnackbar({ open: true, message: 'Please select a Meta message template.', severity: 'warning' });
                    setLoading(false);
                    return;
                }

                const selectedTmplObj = metaTemplates.find(t => t.name === selectedTemplate);
                const varsList = customVariables ? customVariables.split(',').map(v => v.trim()).filter(Boolean) : [];

                payload = {
                    audience: 'custom_list',
                    custom_phones: phoneList,
                    custom_template: selectedTemplate,
                    template_language: selectedTmplObj ? selectedTmplObj.language : 'en',
                    custom_variables: varsList,
                    header_url: headerUrl
                };
            }

            const response = await apiService.sendBulkMessage(payload);
            setSnackbar({ open: true, message: response.data.message || 'Campaign started in the background.', severity: 'success' });

            if (activeTab === 1) {
                setMessage(eventType === 'outage' ? 'an outage occured from the isp , will be repaired soon' : '');
                setLocation('');
                setEstimatedTime('');
            } else {
                setCustomPhonesText('');
                setCustomVariables('');
            }
            // Show the live report; sending carries on even if the page closes.
            if (response.data?.campaign_id) {
                setOpenCampaignId(response.data.campaign_id);
                setActiveTab(3);
            }

        } catch (error) {
            console.error('Error sending message:', error);
            setSnackbar({ 
                open: true, 
                message: error.response?.data?.message || 'Failed to send messages.', 
                severity: 'error' 
            });
        } finally {
            setLoading(false);
        }
    };

    const validPhoneCount = customPhonesText.split(/\r?\n|,/).map(p => p.trim()).filter(p => p.length >= 7).length;
    const selectedTmplObj = metaTemplates.find(t => t.name === selectedTemplate);
    const bodyComp = selectedTmplObj?.components?.find(c => c.type === 'BODY' || c.type === 'body');
    const bodyText = bodyComp?.text || '';
    const matches = bodyText.match(/\{\{\d+\}\}/g);
    const varCount = matches ? new Set(matches).size : 0;
    const headerComp = selectedTmplObj?.components?.find(c => c.type === 'HEADER' || c.type === 'header');
    const headerFormat = headerComp?.format?.toUpperCase() || '';
    const needsMediaHeader = ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(headerFormat);

    return (
        <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3 }}>
                <Typography variant="h5" sx={{ fontWeight: 800, color: '#1a1f3a' }}>
                    Messaging & Meta Campaigns
                </Typography>
                <Button
                    variant="outlined"
                    startIcon={syncing ? <CircularProgress size={16} /> : <SyncIcon />}
                    onClick={handleSyncTemplates}
                    disabled={syncing}
                    sx={{ borderRadius: '12px', fontWeight: 700 }}
                >
                    Sync Meta Templates
                </Button>
            </Box>

            <Paper elevation={0} sx={{ borderRadius: '24px', border: '1px solid rgba(0,0,0,0.06)', overflow: 'hidden', mb: 4 }}>
                <Tabs
                    value={activeTab}
                    onChange={(e, val) => setActiveTab(val)}
                    variant="scrollable"
                    scrollButtons="auto"
                    allowScrollButtonsMobile
                    sx={{ borderBottom: 1, borderColor: 'divider', px: { xs: 0, sm: 3 }, pt: 1, bgcolor: 'rgba(0,0,0,0.01)' }}
                >
                    <Tab label="Inbox" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
                    <Tab label="Customer Notifications" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
                    <Tab label="Marketing Campaign (Custom / Non-Customers)" icon={<WhatsAppIcon sx={{ fontSize: 18 }} />} iconPosition="start" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
                    <Tab label="Campaigns & Reports" icon={<AssessmentIcon sx={{ fontSize: 18 }} />} iconPosition="start" sx={{ fontWeight: 700, fontSize: '0.95rem' }} />
                </Tabs>

                {activeTab === 0 ? (
                    <InboxView openConversationId={openConversationId}
                        headerExtra={<InboxNotificationsControl />}
                        renderComposer={(props) => <Composer key={props.conversation.id} {...props} />} />
                ) : activeTab === 3 ? (
                    <Box sx={{ p: { xs: 2, sm: 4 } }}>
                        <CampaignsView openCampaignId={openCampaignId} onOpened={clearOpenCampaign} />
                    </Box>
                ) : (
                <Box sx={{ p: 4 }}>
                    {activeTab === 1 ? (
                        <>
                            <Alert severity="info" sx={{ mb: 3, borderRadius: '12px' }}>
                                Send automated notification alerts directly to your existing subscribers registered in servicesBills.
                            </Alert>
                            <Grid container spacing={3}>
                                <Grid item xs={12} md={6}>
                                    <FormControl fullWidth>
                                        <InputLabel>Audience</InputLabel>
                                        <Select
                                            value={audience}
                                            onChange={(e) => setAudience(e.target.value)}
                                            label="Audience"
                                        >
                                            <MenuItem value="all">All Customers</MenuItem>
                                            <MenuItem value="active">Active Subscriptions Only</MenuItem>
                                            <MenuItem value="expired">Expired Subscriptions Only</MenuItem>
                                        </Select>
                                    </FormControl>
                                </Grid>
                                
                                <Grid item xs={12} md={6}>
                                    <FormControl fullWidth>
                                        <InputLabel>Message Type</InputLabel>
                                        <Select
                                            value={eventType}
                                            onChange={handleEventTypeChange}
                                            label="Message Type"
                                        >
                                            <MenuItem value="outage">Service Outage</MenuItem>
                                            <MenuItem value="maintenance">Maintenance</MenuItem>
                                            <MenuItem value="feature">New Feature</MenuItem>
                                            <MenuItem value="offer">Special Offer</MenuItem>
                                        </Select>
                                    </FormControl>
                                </Grid>

                                <Grid item xs={12}>
                                    <FormControlLabel
                                        control={
                                            <Switch
                                                checked={excludeResellerCustomers}
                                                onChange={(e) => setExcludeResellerCustomers(e.target.checked)}
                                                color="primary"
                                            />
                                        }
                                        label="Exclude Reseller Customers (Send only to direct customers)"
                                    />
                                </Grid>

                                {(eventType === 'outage' || eventType === 'maintenance') && (
                                    <Grid item xs={12}>
                                        <TextField
                                            fullWidth
                                            select
                                            label="Target Sector (Optional)"
                                            value={targetSector}
                                            onChange={(e) => setTargetSector(e.target.value)}
                                        >
                                            <MenuItem value="">All Sectors</MenuItem>
                                            {sectors.map(s => <MenuItem key={s.id} value={s.name}>{s.name}</MenuItem>)}
                                        </TextField>
                                    </Grid>
                                )}

                                {(eventType === 'outage' || eventType === 'feature' || eventType === 'offer') && (
                                    <Grid item xs={12}>
                                        <TextField
                                            fullWidth
                                            multiline
                                            rows={4}
                                            label="Message Body (Variable 1)"
                                            value={message}
                                            onChange={(e) => setMessage(e.target.value)}
                                            placeholder={eventType === 'outage' ? 'an outage occured from the isp , will be repaired soon' : 'Enter message body'}
                                        />
                                    </Grid>
                                )}

                                {eventType === 'maintenance' && (
                                    <>
                                        <Grid item xs={12} md={6}>
                                            <TextField
                                                fullWidth
                                                label="Location (Variable 1)"
                                                value={location}
                                                onChange={(e) => setLocation(e.target.value)}
                                            />
                                        </Grid>
                                        <Grid item xs={12} md={6}>
                                            <TextField
                                                fullWidth
                                                label="Estimated Time (Variable 2)"
                                                value={estimatedTime}
                                                onChange={(e) => setEstimatedTime(e.target.value)}
                                                placeholder="e.g. 2 hours"
                                            />
                                        </Grid>
                                    </>
                                )}
                            </Grid>
                        </>
                    ) : (
                        <>
                            <Alert severity="success" sx={{ mb: 3, borderRadius: '12px' }}>
                                Reach out to non-customers or targeted marketing lists! Select any Meta-approved message template and upload a TXT/CSV file containing mobile phone numbers.
                            </Alert>

                            <Grid container spacing={3}>
                                <Grid item xs={12} md={6}>
                                    <FormControl fullWidth>
                                        <InputLabel>Select Meta Template</InputLabel>
                                        <Select
                                            value={selectedTemplate}
                                            onChange={(e) => setSelectedTemplate(e.target.value)}
                                            label="Select Meta Template"
                                        >
                                            {metaTemplates.length === 0 ? (
                                                <MenuItem value="" disabled>No templates loaded. Click Sync above.</MenuItem>
                                            ) : (
                                                metaTemplates.map((tmpl, idx) => (
                                                    <MenuItem key={idx} value={tmpl.name}>
                                                        {tmpl.name} ({tmpl.language})
                                                    </MenuItem>
                                                ))
                                            )}
                                        </Select>
                                    </FormControl>
                                </Grid>

                                <Grid item xs={12} md={6}>
                                    <TextField
                                        fullWidth
                                        label="Template Variables (Optional, comma-separated)"
                                        placeholder={varCount > 0 ? `Enter ${varCount} value(s) e.g. Value 1${varCount > 1 ? ', Value 2' : ''}` : "e.g. Summer Promo, 20% Discount"}
                                        value={customVariables}
                                        onChange={(e) => setCustomVariables(e.target.value)}
                                        helperText={
                                            selectedTmplObj && varCount === 0
                                                ? "This Meta template has no variables (static text). Any variables entered here will be ignored automatically."
                                                : selectedTmplObj && varCount > 0
                                                ? `This template expects ${varCount} variable(s) ({{1}} to {{${varCount}}}). Enter comma-separated values.`
                                                : "Replaces placeholders {{1}}, {{2}} in your Meta template."
                                        }
                                    />
                                </Grid>

                                {needsMediaHeader && (
                                    <Grid item xs={12}>
                                        <TextField
                                            fullWidth
                                            label={`Header ${headerFormat} URL (Public HTTPS link required)`}
                                            placeholder={`https://yourdomain.com/sample-${headerFormat.toLowerCase()}.jpg`}
                                            value={headerUrl}
                                            onChange={(e) => setHeaderUrl(e.target.value)}
                                            helperText={`This template has an ${headerFormat} header. Enter a publicly accessible HTTPS URL to your ${headerFormat.toLowerCase()} file.`}
                                            InputProps={{
                                                sx: { borderRadius: '12px', bgcolor: '#f8fafc', fontWeight: 600 }
                                            }}
                                        />
                                    </Grid>
                                )}

                                <Grid item xs={12}>
                                    <Divider sx={{ my: 1 }}>
                                        <Chip label="Target Audience Phone Numbers" sx={{ fontWeight: 700 }} />
                                    </Divider>
                                </Grid>

                                <Grid item xs={12}>
                                    <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
                                        <Button
                                            component="label"
                                            variant="outlined"
                                            startIcon={<UploadFileIcon />}
                                            sx={{ borderRadius: '12px', fontWeight: 700 }}
                                        >
                                            Upload TXT / CSV File
                                            <input
                                                type="file"
                                                accept=".txt,.csv"
                                                hidden
                                                onChange={handleFileUpload}
                                            />
                                        </Button>
                                        <Typography variant="body2" color="text.secondary">
                                            Upload a file with mobile numbers or paste numbers directly below.
                                        </Typography>
                                        {validPhoneCount > 0 && (
                                            <Chip label={`${validPhoneCount} Valid Phone(s) Ready`} color="primary" size="small" sx={{ fontWeight: 700 }} />
                                        )}
                                    </Stack>

                                    <TextField
                                        fullWidth
                                        multiline
                                        rows={6}
                                        label="Mobile Numbers (One per line or comma-separated)"
                                        placeholder="e.g.&#10;+12345678901&#10;+19876543210"
                                        value={customPhonesText}
                                        onChange={(e) => setCustomPhonesText(e.target.value)}
                                    />
                                </Grid>
                            </Grid>
                        </>
                    )}

                    <Box sx={{ mt: 4, pt: 2, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
                        <Button
                            variant="contained"
                            size="large"
                            startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <SendIcon />}
                            onClick={handleSend}
                            disabled={loading}
                            sx={{ borderRadius: '12px', px: 5, py: 1.5, fontWeight: 700 }}
                        >
                            {loading ? 'Starting...' : activeTab === 1 ? 'Send Customer Notification' : `Send Marketing Campaign (${validPhoneCount} Recipients)`}
                        </Button>
                    </Box>
                </Box>
                )}
            </Paper>

        </Box>
    );
};

export default MessagingView;
