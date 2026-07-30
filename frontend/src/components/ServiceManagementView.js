import React, { useState, useEffect, useCallback } from 'react';
import {
  Box, Container, Typography, Paper, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Button, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, Select, MenuItem, FormControl,
  InputLabel, Grid, Chip, IconButton, Tooltip, alpha, useTheme,
  Divider, CircularProgress, Fade, Autocomplete
} from '@mui/material';
import {
  Add as AddIcon, Refresh as RefreshIcon, Edit as EditIcon,
  Delete as DeleteIcon, CheckCircle as CheckCircleIcon,
  Warning as WarningIcon, BugReport as BugIcon,
  WifiOff as OutageIcon, PeopleAlt as PeopleIcon,
  Close as CloseIcon, Done as DoneIcon,
  NotificationsActive as NotificationsIcon,
  History as HistoryIcon
} from '@mui/icons-material';
import { useAppContext } from '../context/AppContext.js';
import * as serviceWorkerRegistration from '../serviceWorkerRegistration';

// ── Helpers ─────────────────────────────────────────────────────────────────
const STATUS_COLORS = {
  active: 'success', suspended: 'warning', terminated: 'error',
  open: 'error', in_progress: 'warning', resolved: 'success', closed: 'default',
};
const PRIORITY_COLORS = { critical: 'error', high: 'warning', medium: 'info', low: 'success' };

const statusChip = (status) => (
  <Chip label={status?.replace('_', ' ')} color={STATUS_COLORS[status] || 'default'} size="small"
    sx={{ textTransform: 'capitalize', fontWeight: 600 }} />
);
const priorityChip = (priority) => (
  <Chip label={priority} color={PRIORITY_COLORS[priority] || 'default'} size="small"
    sx={{ textTransform: 'capitalize', fontWeight: 600 }} />
);

// ── Section Header ────────────────────────────────────────────────────────────
const SectionHeader = ({ icon, title, count, color, action }) => {
  const theme = useTheme();
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box sx={{ width: 40, height: 40, borderRadius: '12px', bgcolor: alpha(color, 0.12), display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {React.cloneElement(icon, { sx: { color, fontSize: 20 } })}
        </Box>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700, lineHeight: 1.2 }}>{title}</Typography>
          {count !== undefined && <Typography variant="caption" color="text.secondary">{count} record{count !== 1 ? 's' : ''}</Typography>}
        </Box>
      </Box>
      {action}
    </Box>
  );
};

// ── Main Component ────────────────────────────────────────────────────────────
const ServiceManagementView = () => {
  const { apiService } = useAppContext();
  const theme = useTheme();

  const [customers, setCustomers] = useState([]);
  const [serviceStatuses, setServiceStatuses] = useState([]);
  const [tickets, setTickets] = useState([]);
  const [outages, setOutages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [pushSubscribed, setPushSubscribed] = useState(false);

  // Ticket dialog
  const [ticketDialog, setTicketDialog] = useState({ open: false, mode: 'create', data: null });
  const [ticketForm, setTicketForm] = useState({ customer_id: '', title: '', description: '', priority: 'medium' });

  // Outage dialog
  const [outageDialog, setOutageDialog] = useState({ open: false, mode: 'create', data: null });
  const [outageForm, setOutageForm] = useState({ title: '', description: '', affected_areas: '' });

  // Status edit dialog
  const [statusDialog, setStatusDialog] = useState({ open: false, data: null });
  const [statusForm, setStatusForm] = useState({ status: '', notes: '' });

  // Logs dialog
  const [logsDialog, setLogsDialog] = useState({ open: false, logs: [], ticketTitle: '' });

  // Filters
  const [ticketFilter, setTicketFilter] = useState('');
  const [outageFilter, setOutageFilter] = useState('all');

  // ── Fetch ──────────────────────────────────────────────────────────────────
  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [cRes, sRes, tRes, oRes] = await Promise.all([
        apiService.fetchCustomers(),
        apiService.fetchServiceStatuses(),
        apiService.fetchSupportTickets(),
        apiService.fetchServiceOutages(),
      ]);
      setCustomers(cRes.customers || []);
      setServiceStatuses(Array.isArray(sRes.data) ? sRes.data : []);
      const td = tRes.data;
      setTickets(Array.isArray(td?.tickets) ? td.tickets : Array.isArray(td) ? td : []);
      setOutages(Array.isArray(oRes.data) ? oRes.data : []);
    } catch (e) {
      console.error('Error loading service management data:', e);
    } finally {
      setLoading(false);
    }
  }, [apiService]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  useEffect(() => {
    // Check if push is already subscribed
    if ('serviceWorker' in navigator && 'PushManager' in window) {
      navigator.serviceWorker.ready.then(reg => {
        reg.pushManager.getSubscription().then(sub => {
          if (sub) setPushSubscribed(true);
        });
      });
    }
  }, []);

  const handleSubscribePush = async () => {
    try {
      const res = await apiService.api.get('/vapid-public-key');
      const { public_key } = res.data;
      const sub = await serviceWorkerRegistration.subscribeUserToPush(public_key);
      await apiService.api.post('/push-subscribe', { subscription: sub });
      setPushSubscribed(true);
      alert('Push notifications enabled!');
    } catch (e) {
      console.error('Failed to subscribe:', e);
      const reason = e?.response?.data?.msg || e?.message || 'Unknown error';
      alert(`Could not enable push notifications: ${reason}`);
    }
  };

  // ── Ticket handlers ────────────────────────────────────────────────────────
  const openCreateTicket = () => {
    setTicketForm({ customer_id: '', title: '', description: '', priority: 'medium' });
    setTicketDialog({ open: true, mode: 'create', data: null });
  };
  const openEditTicket = (t) => {
    setTicketForm({ customer_id: t.customer_id, title: t.title, description: t.description || '', priority: t.priority });
    setTicketDialog({ open: true, mode: 'edit', data: t });
  };
  const handleSaveTicket = async () => {
    try {
      if (ticketDialog.mode === 'create') {
        await apiService.createSupportTicket(ticketForm);
      } else {
        await apiService.updateSupportTicket(ticketDialog.data.id, ticketForm);
      }
      setTicketDialog({ open: false, mode: 'create', data: null });
      fetchAll();
    } catch (e) { console.error(e); }
  };
  const handleUpdateTicketStatus = async (id, status) => {
    try { await apiService.updateSupportTicket(id, { status }); fetchAll(); }
    catch (e) { console.error(e); }
  };
  const handleDeleteTicket = async (id) => {
    if (!window.confirm('Delete this ticket?')) return;
    try { await apiService.deleteSupportTicket(id); fetchAll(); }
    catch (e) { console.error(e); }
  };
  const handleViewLogs = (ticket) => {
    setLogsDialog({ open: true, logs: ticket.logs || [], ticketTitle: ticket.title });
  };

  // ── Outage handlers ────────────────────────────────────────────────────────
  const openCreateOutage = () => {
    setOutageForm({ title: '', description: '', affected_areas: '' });
    setOutageDialog({ open: true, mode: 'create', data: null });
  };
  const openEditOutage = (o) => {
    setOutageForm({ title: o.title, description: o.description, affected_areas: o.affected_areas });
    setOutageDialog({ open: true, mode: 'edit', data: o });
  };
  const handleSaveOutage = async () => {
    try {
      if (outageDialog.mode === 'create') {
        await apiService.createServiceOutage({ ...outageForm, start_time: new Date().toISOString().replace('T', ' ').slice(0, 19) });
      } else {
        await apiService.updateServiceOutage(outageDialog.data.id, outageForm);
      }
      setOutageDialog({ open: false, mode: 'create', data: null });
      fetchAll();
    } catch (e) { console.error(e); }
  };
  const handleResolveOutage = async (id) => {
    try { await apiService.updateServiceOutage(id, { status: 'resolved' }); fetchAll(); }
    catch (e) { console.error(e); }
  };

  // ── Status handlers ────────────────────────────────────────────────────────
  const openEditStatus = (s) => {
    setStatusForm({ status: s.status, notes: s.notes || '' });
    setStatusDialog({ open: true, data: s });
  };
  const handleSaveStatus = async () => {
    try {
      await apiService.updateServiceStatusById(statusDialog.data.id, statusForm);
      setStatusDialog({ open: false, data: null });
      fetchAll();
    } catch (e) { console.error(e); }
  };

  // ── Filtered data ──────────────────────────────────────────────────────────
  const filteredTickets = tickets.filter(t => !ticketFilter || t.status === ticketFilter);
  const filteredOutages = outages.filter(o => outageFilter === 'all' || o.status === outageFilter);

  const sectionPaper = {
    p: 3, borderRadius: '20px',
    background: 'linear-gradient(135deg, #ffffff 0%, #f8fafc 100%)',
    border: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
    mb: 3
  };

  if (loading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
      <CircularProgress size={48} />
    </Box>
  );

  return (
    <Box sx={{ p: { xs: 1.5, sm: 2, md: 3 }, background: 'linear-gradient(135deg, #f6f9fc 0%, #ffffff 100%)', minHeight: '100vh' }}>

      {/* ── Header ── */}
      <Paper elevation={0} sx={{ p: { xs: 2, sm: 3, md: 4 }, mb: 4, borderRadius: '24px', background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color: 'white', position: 'relative', overflow: 'hidden' }}>
        <Box sx={{ position: 'absolute', top: -50, right: -50, width: 200, height: 200, borderRadius: '50%', background: alpha('#ffffff', 0.1) }} />
        <Box sx={{ position: 'relative', zIndex: 1 }}>
          <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, justifyContent: 'space-between', alignItems: { xs: 'stretch', md: 'flex-start' }, flexWrap: 'wrap', gap: { xs: 2, md: 0 } }}>
            <Box>
              <Typography variant="h4" sx={{ fontWeight: 700, mb: 1, fontSize: { xs: '1.3rem', sm: '1.75rem', md: '2.125rem' } }}>Service Management</Typography>
              <Typography variant="body1" sx={{ opacity: 0.9, mb: 3, fontSize: { xs: '0.85rem', sm: '1rem' } }}>Monitor service health, manage support tickets and report outages</Typography>
            </Box>
            {!pushSubscribed && (
              <Button 
                variant="outlined" 
                sx={{ color: 'white', borderColor: 'white', borderRadius: '12px', width: { xs: '100%', md: 'auto' } }} 
                startIcon={<NotificationsIcon />}
                onClick={handleSubscribePush}
              >
                Enable Notifications
              </Button>
            )}
          </Box>
          <Box sx={{ display: 'flex', gap: { xs: 2, sm: 3 }, flexWrap: 'wrap' }}>
            {[
              { label: 'Customers', value: serviceStatuses.length, icon: <PeopleIcon sx={{ fontSize: 20 }} /> },
              { label: 'Open Tickets', value: tickets.filter(t => t.status === 'open').length, icon: <BugIcon sx={{ fontSize: 20 }} /> },
              { label: 'Active Outages', value: outages.filter(o => o.status === 'active').length, icon: <OutageIcon sx={{ fontSize: 20 }} /> },
            ].map((stat, i) => (
              <React.Fragment key={stat.label}>
                {i > 0 && <Divider orientation="vertical" flexItem sx={{ bgcolor: 'rgba(255,255,255,0.3)' }} />}
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  {stat.icon}
                  <Box>
                    <Typography variant="caption" sx={{ opacity: 0.8, display: 'block' }}>{stat.label}</Typography>
                    <Typography variant="h6" sx={{ fontWeight: 700 }}>{stat.value}</Typography>
                  </Box>
                </Box>
              </React.Fragment>
            ))}
          </Box>
        </Box>
      </Paper>

      {/* ── Service Status ── */}
      <Paper elevation={0} sx={sectionPaper}>
        <SectionHeader icon={<PeopleIcon />} title="Service Status" count={serviceStatuses.length}
          color={theme.palette.primary.main}
          action={<Button startIcon={<RefreshIcon />} onClick={fetchAll} size="small" variant="outlined" sx={{ borderRadius: '10px', textTransform: 'none' }}>Refresh</Button>}
        />
        {serviceStatuses.length === 0 ? (
          <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>No service statuses recorded yet.</Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: alpha(theme.palette.primary.main, 0.04) }}>
                  <TableCell sx={{ fontWeight: 700 }}>Customer</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Last Updated</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Notes</TableCell>
                  <TableCell sx={{ fontWeight: 700, width: 80 }}>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {serviceStatuses.map(s => (
                  <TableRow key={s.id} hover>
                    <TableCell sx={{ fontWeight: 600 }}>{s.customer_name}</TableCell>
                    <TableCell>{statusChip(s.status)}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem' }}>{new Date(s.last_updated).toLocaleString()}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.notes || '—'}</TableCell>
                    <TableCell>
                      <Tooltip title="Edit Status">
                        <IconButton size="small" color="primary" onClick={() => openEditStatus(s)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* ── Support Tickets ── */}
      <Paper elevation={0} sx={sectionPaper}>
        <SectionHeader icon={<BugIcon />} title="Support Tickets" count={filteredTickets.length}
          color={theme.palette.error.main}
          action={
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
              <Select value={ticketFilter} onChange={e => setTicketFilter(e.target.value)} size="small" displayEmpty sx={{ minWidth: 130, borderRadius: '10px' }}>
                <MenuItem value="">All Statuses</MenuItem>
                <MenuItem value="open">Open</MenuItem>
                <MenuItem value="in_progress">In Progress</MenuItem>
                <MenuItem value="resolved">Resolved</MenuItem>
                <MenuItem value="closed">Closed</MenuItem>
              </Select>
              <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={openCreateTicket}
                sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
                New Ticket
              </Button>
            </Box>
          }
        />
        {filteredTickets.length === 0 ? (
          <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>No tickets found.</Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: alpha(theme.palette.error.main, 0.04) }}>
                  <TableCell sx={{ fontWeight: 700 }}>#</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Customer</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Title</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Priority</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Timeline & Tracking</TableCell>
                  <TableCell sx={{ fontWeight: 700, width: 140 }}>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredTickets.map(t => (
                  <TableRow key={t.id} hover>
                    <TableCell sx={{ color: 'text.secondary' }}>#{t.id}</TableCell>
                    <TableCell sx={{ fontWeight: 600 }}>{t.customer_name || `ID:${t.customer_id}`}</TableCell>
                    <TableCell sx={{ maxWidth: 200 }}>
                      <Typography variant="body2" title={t.description} sx={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 180 }}>{t.title}</Typography>
                    </TableCell>
                    <TableCell>{priorityChip(t.priority)}</TableCell>
                    <TableCell>{statusChip(t.status)}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.78rem' }}>
                      <Box><strong>Created:</strong> {new Date(t.created_at).toLocaleDateString()}</Box>
                      {t.in_progress_at && <Box><strong>In Prog:</strong> {new Date(t.in_progress_at).toLocaleDateString()} {t.in_progress_by && `(${t.in_progress_by})`}</Box>}
                      {t.resolved_at && (
                        <Box>
                          <strong>Resolved:</strong> {new Date(t.resolved_at).toLocaleDateString()} {t.resolved_by && `(${t.resolved_by})`}
                          <br/>
                          <Typography variant="caption" color="primary">
                            {t.in_progress_at ? `Time taken: ${Math.round((new Date(t.resolved_at) - new Date(t.in_progress_at)) / 60000)} mins` : ''}
                          </Typography>
                        </Box>
                      )}
                    </TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {t.status === 'open' && (
                          <Tooltip title="Mark In Progress">
                            <IconButton size="small" color="warning" onClick={() => handleUpdateTicketStatus(t.id, 'in_progress')}>
                              <WarningIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {(t.status === 'open' || t.status === 'in_progress') && (
                          <Tooltip title="Mark Resolved">
                            <IconButton size="small" color="success" onClick={() => handleUpdateTicketStatus(t.id, 'resolved')}>
                              <CheckCircleIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {t.status === 'resolved' && (
                          <Tooltip title="Close Ticket">
                            <IconButton size="small" onClick={() => handleUpdateTicketStatus(t.id, 'closed')}>
                              <DoneIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        <Tooltip title="Edit">
                          <IconButton size="small" color="primary" onClick={() => openEditTicket(t)}>
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="Delete">
                          <IconButton size="small" color="error" onClick={() => handleDeleteTicket(t.id)}>
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip title="View Logs">
                          <IconButton size="small" color="info" onClick={() => handleViewLogs(t)}>
                            <HistoryIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* ── Service Outages ── */}
      <Paper elevation={0} sx={sectionPaper}>
        <SectionHeader icon={<OutageIcon />} title="Service Outages" count={filteredOutages.length}
          color={theme.palette.warning.main}
          action={
            <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
              <Select value={outageFilter} onChange={e => setOutageFilter(e.target.value)} size="small" sx={{ minWidth: 130, borderRadius: '10px' }}>
                <MenuItem value="all">All</MenuItem>
                <MenuItem value="active">Active</MenuItem>
                <MenuItem value="resolved">Resolved</MenuItem>
              </Select>
              <Button startIcon={<AddIcon />} variant="contained" color="warning" size="small" onClick={openCreateOutage}
                sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
                Report Outage
              </Button>
            </Box>
          }
        />
        {filteredOutages.length === 0 ? (
          <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>No outages found.</Typography>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow sx={{ bgcolor: alpha(theme.palette.warning.main, 0.04) }}>
                  <TableCell sx={{ fontWeight: 700 }}>Title</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Affected Areas</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Started</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Resolved At</TableCell>
                  <TableCell sx={{ fontWeight: 700 }}>Status</TableCell>
                  <TableCell sx={{ fontWeight: 700, width: 120 }}>Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filteredOutages.map(o => (
                  <TableRow key={o.id} hover>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{o.title}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.description}</Typography>
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem' }}>{o.affected_areas}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem' }}>{o.start_time ? new Date(o.start_time).toLocaleString() : '—'}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem' }}>{o.end_time ? new Date(o.end_time).toLocaleString() : '—'}</TableCell>
                    <TableCell>{statusChip(o.status)}</TableCell>
                    <TableCell>
                      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                        {o.status === 'active' && (
                          <Tooltip title="Mark Resolved">
                            <IconButton size="small" color="success" onClick={() => handleResolveOutage(o.id)}>
                              <CheckCircleIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        <Tooltip title="Edit">
                          <IconButton size="small" color="primary" onClick={() => openEditOutage(o)}>
                            <EditIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </Paper>

      {/* ── Ticket Dialog ── */}
      <Dialog open={ticketDialog.open} onClose={() => setTicketDialog(d => ({ ...d, open: false }))} maxWidth="sm" fullWidth PaperProps={{ sx: { borderRadius: '20px' } }}>
        <DialogTitle sx={{ fontWeight: 700 }}>{ticketDialog.mode === 'create' ? 'New Support Ticket' : 'Edit Ticket'}</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12}>
              <Autocomplete
                size="small"
                options={customers}
                getOptionLabel={(option) => option.name || `ID: ${option.id}`}
                value={customers.find(c => c.id === ticketForm.customer_id) || null}
                onChange={(e, newValue) => {
                  setTicketForm(f => ({ ...f, customer_id: newValue ? newValue.id : '' }));
                }}
                renderInput={(params) => <TextField {...params} label="Customer" variant="outlined" />}
              />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth label="Title" size="small" value={ticketForm.title} onChange={e => setTicketForm(f => ({ ...f, title: e.target.value }))} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth multiline rows={3} label="Description" size="small" value={ticketForm.description} onChange={e => setTicketForm(f => ({ ...f, description: e.target.value }))} />
            </Grid>
            <Grid item xs={12}>
              <FormControl fullWidth size="small">
                <InputLabel>Priority</InputLabel>
                <Select value={ticketForm.priority} label="Priority" onChange={e => setTicketForm(f => ({ ...f, priority: e.target.value }))}>
                  <MenuItem value="low">Low</MenuItem>
                  <MenuItem value="medium">Medium</MenuItem>
                  <MenuItem value="high">High</MenuItem>
                  <MenuItem value="critical">Critical</MenuItem>
                </Select>
              </FormControl>
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setTicketDialog(d => ({ ...d, open: false }))} sx={{ textTransform: 'none' }}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveTicket} sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
            {ticketDialog.mode === 'create' ? 'Create' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Outage Dialog ── */}
      <Dialog open={outageDialog.open} onClose={() => setOutageDialog(d => ({ ...d, open: false }))} maxWidth="sm" fullWidth PaperProps={{ sx: { borderRadius: '20px' } }}>
        <DialogTitle sx={{ fontWeight: 700 }}>{outageDialog.mode === 'create' ? 'Report Service Outage' : 'Edit Outage'}</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12}>
              <TextField fullWidth label="Title" size="small" value={outageForm.title} onChange={e => setOutageForm(f => ({ ...f, title: e.target.value }))} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth multiline rows={3} label="Description" size="small" value={outageForm.description} onChange={e => setOutageForm(f => ({ ...f, description: e.target.value }))} />
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth label="Affected Areas" size="small" value={outageForm.affected_areas} onChange={e => setOutageForm(f => ({ ...f, affected_areas: e.target.value }))} helperText="e.g. Downtown, Zone A, Block 5" />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setOutageDialog(d => ({ ...d, open: false }))} sx={{ textTransform: 'none' }}>Cancel</Button>
          <Button variant="contained" color="warning" onClick={handleSaveOutage} sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>
            {outageDialog.mode === 'create' ? 'Report' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Service Status Edit Dialog ── */}
      <Dialog open={statusDialog.open} onClose={() => setStatusDialog({ open: false, data: null })} maxWidth="xs" fullWidth PaperProps={{ sx: { borderRadius: '20px' } }}>
        <DialogTitle sx={{ fontWeight: 700 }}>Update Service Status — {statusDialog.data?.customer_name}</DialogTitle>
        <DialogContent>
          <Grid container spacing={2} sx={{ mt: 0.5 }}>
            <Grid item xs={12}>
              <FormControl fullWidth size="small">
                <InputLabel>Status</InputLabel>
                <Select value={statusForm.status} label="Status" onChange={e => setStatusForm(f => ({ ...f, status: e.target.value }))}>
                  <MenuItem value="active">Active</MenuItem>
                  <MenuItem value="suspended">Suspended</MenuItem>
                  <MenuItem value="terminated">Terminated</MenuItem>
                </Select>
              </FormControl>
            </Grid>
            <Grid item xs={12}>
              <TextField fullWidth multiline rows={2} label="Notes" size="small" value={statusForm.notes} onChange={e => setStatusForm(f => ({ ...f, notes: e.target.value }))} />
            </Grid>
          </Grid>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setStatusDialog({ open: false, data: null })} sx={{ textTransform: 'none' }}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveStatus} sx={{ borderRadius: '10px', textTransform: 'none', fontWeight: 600 }}>Save</Button>
        </DialogActions>
      </Dialog>

      {/* ── Ticket Logs Dialog ── */}
      <Dialog open={logsDialog.open} onClose={() => setLogsDialog({ open: false, logs: [], ticketTitle: '' })} maxWidth="sm" fullWidth PaperProps={{ sx: { borderRadius: '20px' } }}>
        <DialogTitle sx={{ fontWeight: 700 }}>Ticket Logs — {logsDialog.ticketTitle}</DialogTitle>
        <DialogContent>
          {logsDialog.logs.length === 0 ? (
            <Typography color="text.secondary" sx={{ textAlign: 'center', py: 4 }}>No logs available for this ticket.</Typography>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow sx={{ bgcolor: alpha(theme.palette.info.main, 0.04) }}>
                    <TableCell sx={{ fontWeight: 700 }}>Time</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Action</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>User</TableCell>
                    <TableCell sx={{ fontWeight: 700 }}>Details</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {logsDialog.logs.map((log, index) => (
                    <TableRow key={index} hover>
                      <TableCell sx={{ color: 'text.secondary', fontSize: '0.82rem', whiteSpace: 'nowrap' }}>{log.timestamp}</TableCell>
                      <TableCell sx={{ fontWeight: 600, fontSize: '0.85rem' }}>{log.action}</TableCell>
                      <TableCell sx={{ fontSize: '0.85rem' }}>{log.username}</TableCell>
                      <TableCell sx={{ color: 'text.secondary', fontSize: '0.85rem' }}>{log.details}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={() => setLogsDialog({ open: false, logs: [], ticketTitle: '' })} sx={{ textTransform: 'none' }}>Close</Button>
        </DialogActions>
      </Dialog>

    </Box>
  );
};

export default ServiceManagementView;