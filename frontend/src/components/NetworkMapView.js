import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Box, Typography, Alert, CircularProgress, ToggleButton,
         ToggleButtonGroup, Paper, Button, Dialog, DialogTitle, DialogContent,
         DialogActions, TextField, MenuItem, Autocomplete, Stack, List,
         ListItem, ListItemText } from '@mui/material';
import { MapContainer, TileLayer, Marker, Popup, Tooltip, Polyline,
         useMapEvents } from 'react-leaflet';
import L from 'leaflet';
// apiService is a direct module export of AppContext.js, not part of the
// hook's value -- same convention as NetworkTreeView.js/
// NetworkDeviceManagementView.js. There is no generic apiService.get(); the
// raw axios instance lives at apiService.api (see ServiceManagementView.js
// for the same apiService.api.get(...) pattern used for an endpoint that
// has no dedicated named method yet), and its baseURL already carries
// '/api', so paths here start at '/network-map', not '/api/network-map'.
import { apiService, useAppContext } from '../context/AppContext';
import { spanStyle, nodeMarkerStyle, TILE_LAYERS } from './fiberMapStyles';
import { formatStamp } from './formatStamp';
import 'leaflet/dist/leaflet.css';
import './networkMap.css';

// Tripoli / Koura -- where DeltaNet's plant is. Only used when a tenant has no
// nodes placed yet; once a root exists the map centres on it.
const DEFAULT_CENTER = [34.4367, 35.8497];

// Every write route on the map is admin_or_finance_required() in app.py; the
// read routes deliberately also admit employee/collector so field staff can
// see the map. Gating every control (and every map-click handler) on this,
// rather than rendering a control that would just 403 on click, is the
// whole point of the role prop.
const EDIT_ROLES = ['admin', 'finance'];

// Separator- and case-insensitive, matching the backend's _normalize_mac:
// strip everything but hex digits before comparing, so 'aa:aa:aa:aa:aa:aa'
// and 'AA-AA-AA-AA-AA-AA' compare equal here the same way they do server-side.
function normalizeMacJs(mac) {
  return (mac || '').toLowerCase().replace(/[^0-9a-f]/g, '');
}

// nodeMarkerStyle's fillOpacity is meant as CircleMarker-style fill opacity --
// the fill can fade while the outline stays put -- but CSS `opacity` on the
// div fades the *whole element*, border included. Baking the alpha into the
// background color instead (rather than the div's opacity) keeps the white
// border fully opaque while only the fill dims.
function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function nodeDivIcon(kind, status) {
  // A CircleMarker (used for the read-only Task 6 view) is a Leaflet Path,
  // and Leaflet Paths cannot be dragged without an extra plugin -- there is
  // no such plugin in this project's dependencies. A Marker with a small
  // divIcon gets real dragging for free from Leaflet core, so placed nodes
  // are drawn as Markers here; fiberMapStyles' color/radius/fillOpacity
  // still drive the look, just baked into the icon's inline style instead
  // of pathOptions.
  const { color, radius, fillOpacity } = nodeMarkerStyle(kind, status);
  const d = radius * 2;
  return L.divIcon({
    className: 'fiber-node-icon',
    html: `<div style="width:${d}px;height:${d}px;border-radius:50%;` +
          `background:${hexToRgba(color, fillOpacity)};border:2px solid #fff;` +
          `box-shadow:0 0 2px rgba(0,0,0,.6);"></div>`,
    iconSize: [d, d],
    iconAnchor: [d / 2, d / 2],
  });
}

// A child of MapContainer purely so it can call useMapEvents -- react-leaflet
// has no onClick prop on MapContainer itself, only this hook, and hooks may
// only run inside a component that is itself inside the MapContainer's
// LeafletContext. Renders nothing.
function MapClickCapture({ onMapClick }) {
  useMapEvents({ click: (e) => onMapClick(e.latlng) });
  return null;
}

export default function NetworkMapView({ oltDeviceId, userRole }) {
  const { setSnackbar } = useAppContext();
  const canEdit = EDIT_ROLES.includes(userRole);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [layerKey, setLayerKey] = useState('satellite');
  const [unplaced, setUnplaced] = useState([]);

  // Placement UX state. addMode arms the next map click; pendingParentId is
  // set by "Draw from here" and makes that next click a child of a specific
  // node rather than a fresh root.
  const [addMode, setAddMode] = useState(false);
  const [pendingParentId, setPendingParentId] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await apiService.api.get('/network-map', {
        params: { olt_device_id: oltDeviceId },
      });
      setData(res.data);
    } catch (err) {
      setSnackbar({ open: true, severity: 'error',
                    message: 'Could not load the network map.' });
    } finally {
      setLoading(false);
    }
  }, [oltDeviceId, setSnackbar]);

  // Open to every role that can see the map, not just canEdit -- "so staff
  // can see what remains" is a field-staff use case too, and the endpoint
  // itself is admitted to employee/collector (see network_view_required()).
  const loadUnplaced = useCallback(async () => {
    try {
      const res = await apiService.api.get('/network-map/unplaced-onus', {
        params: { olt_device_id: oltDeviceId },
      });
      setUnplaced(res.data.onus || []);
    } catch {
      // The main map load above already surfaces a hard failure; this panel
      // is a secondary aid and just stays empty rather than piling on a
      // second error message for the same underlying problem.
    }
  }, [oltDeviceId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadUnplaced(); }, [loadUnplaced]);

  const placedMacs = useMemo(() => new Set(
    (data?.nodes || [])
      .map((n) => n.onu_mac)
      .filter(Boolean)
      .map(normalizeMacJs)
  ), [data]);
  // Defence in depth: get_unplaced_onus already excludes anything with a
  // node server-side, but this page's own `data.nodes` is sometimes fresher
  // (e.g. right after a node was just placed, before the next
  // unplaced-onus fetch lands) -- re-filtering here means a stale response
  // can never show an ONU as unplaced when this page already knows better.
  const unplacedFiltered = useMemo(() => unplaced.filter(
    (o) => o.mac_address && !placedMacs.has(normalizeMacJs(o.mac_address))
  ), [unplaced, placedMacs]);

  const closeDialog = () => { if (!saving) setDialog(null); };

  const handleMapClick = useCallback((latlng) => {
    if (!canEdit || !addMode) return;
    const nodes = data?.nodes || [];
    if (nodes.length === 0) {
      // The API enforces one root per OLT; with none placed yet, root is
      // the only offered kind, so the dialog doesn't even show a selector.
      setDialog({ mode: 'create', kind: 'root', label: 'Control room', onuMac: '',
                  parentId: null, lat: latlng.lat, lng: latlng.lng });
      return;
    }
    if (pendingParentId == null) {
      setSnackbar({ open: true, severity: 'info', message:
        'Select a placed node and click "Draw from here" before placing the next one.' });
      return;
    }
    setDialog({ mode: 'create', kind: 'junction', label: '', onuMac: '',
                parentId: pendingParentId, lat: latlng.lat, lng: latlng.lng });
  }, [canEdit, addMode, data, pendingParentId, setSnackbar]);

  const startDrawFromHere = (node) => {
    setPendingParentId(node.id);
    setAddMode(true);
    setSnackbar({ open: true, severity: 'info', message:
      `Click the map to place the next node down the line from "${node.label}".` });
  };

  const openEditDialog = (node) => {
    setDialog({ mode: 'edit', nodeId: node.id, kind: node.kind,
                label: node.label, onuMac: node.onu_mac || '',
                originalOnuMac: node.onu_mac || '' });
  };

  const submitDialog = async () => {
    if (!dialog) return;
    const label = (dialog.label || '').trim();
    if (!label) {
      setSnackbar({ open: true, severity: 'error', message: 'Label is required.' });
      return;
    }
    if (dialog.kind === 'onu' && !dialog.onuMac) {
      setSnackbar({ open: true, severity: 'error', message: 'Select which ONU this is.' });
      return;
    }
    setSaving(true);
    try {
      if (dialog.mode === 'edit') {
        await apiService.api.put(`/network-map/nodes/${dialog.nodeId}`, {
          kind: dialog.kind,
          label,
          onu_mac: dialog.kind === 'onu' ? dialog.onuMac : null,
        });
        setSnackbar({ open: true, severity: 'success', message: 'Node updated.' });
      } else {
        await apiService.api.post('/network-map/nodes', {
          olt_device_id: oltDeviceId,
          kind: dialog.kind,
          label,
          latitude: dialog.lat,
          longitude: dialog.lng,
          parent_node_id: dialog.parentId,
          ...(dialog.kind === 'onu' ? { onu_mac: dialog.onuMac } : {}),
        });
        setSnackbar({ open: true, severity: 'success', message: 'Node placed.' });
      }
      setDialog(null);
      setAddMode(false);
      setPendingParentId(null);
      // Reload without ever clearing `data` to null first -- Tree v2 shipped
      // a page that blanked its data on every refresh and flashed the whole
      // view away; here that would mean the entire map disappearing every
      // time a pin is dropped.
      await load();
      await loadUnplaced();
    } catch (err) {
      const message = err?.response?.data?.message ||
        (dialog.mode === 'edit' ? 'Could not update the node.' : 'Could not place the node.');
      setSnackbar({ open: true, severity: 'error', message });
    } finally {
      setSaving(false);
    }
  };

  const handleDragEnd = async (node, event) => {
    const { lat, lng } = event.target.getLatLng();
    try {
      await apiService.api.put(`/network-map/nodes/${node.id}`,
        { latitude: lat, longitude: lng });
      await load();
    } catch (err) {
      setSnackbar({ open: true, severity: 'error', message:
        'Could not move the node -- reloading its last saved position.' });
      await load();
    }
  };

  const handleDelete = async (node) => {
    try {
      await apiService.api.delete(`/network-map/nodes/${node.id}`);
      setSnackbar({ open: true, severity: 'success', message: `Deleted "${node.label}".` });
      if (pendingParentId === node.id) setPendingParentId(null);
      await load();
      await loadUnplaced();
    } catch (err) {
      // A 409 names the children still attached to this node -- surfaced
      // verbatim, since that message is the entire point of the response.
      const message = err?.response?.data?.message || 'Could not delete the node.';
      setSnackbar({ open: true, severity: 'error', message });
    }
  };

  const toggleAddMode = () => {
    setAddMode((prev) => {
      const next = !prev;
      if (!next) setPendingParentId(null);
      return next;
    });
  };

  if (loading) return <CircularProgress />;
  if (!data) return <Alert severity="error">The network map is unavailable.</Alert>;

  // `data` can be truthy and still not be the payload shape this page
  // expects -- e.g. the service worker's offline fallback resolves any
  // failed fetch to a fake 200 whose body is the plain string
  // "You are offline.", which is truthy and sails past the `!data` check
  // above. Defaulting every read here means a malformed payload degrades to
  // an empty map instead of throwing and white-screening the whole app.
  const nodes = data.nodes || [];
  const spans = data.spans || [];
  const orphans = data.orphans || [];
  const distanceWarnings = data.distance_warnings || [];
  const nodeStatus = data.node_status || {};

  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const root = nodes.find((n) => n.kind === 'root');
  const center = root ? [root.latitude, root.longitude] : DEFAULT_CENTER;
  const layer = TILE_LAYERS.find((l) => l.key === layerKey) || TILE_LAYERS[0];
  const boundary = spans.filter((s) => s.is_fault_boundary);
  const pendingParent = pendingParentId != null ? byId[pendingParentId] : null;

  // The node's own onu_mac (fixed at dialog-open time via originalOnuMac,
  // NOT the live dialog.onuMac, which gets cleared to '' on every kind
  // change) is never in unplacedFiltered -- it's already placed, on itself.
  // Keeping it available as a synthetic option means toggling kind away
  // from 'onu' and back doesn't strand the user with no way to re-select
  // their own node's original ONU.
  const editSyntheticOnuOption = (dialog?.mode === 'edit' && dialog.originalOnuMac
      && !unplacedFiltered.some((o) => o.mac_address === dialog.originalOnuMac))
    ? { mac_address: dialog.originalOnuMac, description: dialog.label }
    : null;
  const onuAutocompleteOptions = editSyntheticOnuOption
    ? [editSyntheticOnuOption, ...unplacedFiltered]
    : unplacedFiltered;

  return (
    <Box>
      <Typography variant="h5" gutterBottom>Network Map</Typography>

      {boundary.length > 0 && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {boundary.length === 1
            ? `Fault localised to the span into "${byId[boundary[0].child_node_id]?.label}".`
            : `${boundary.length} separate faults localised.`}
        </Alert>
      )}

      {orphans.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {orphans.length} node(s) are not connected to the control room --
          their spans are not drawn. Their parent links form a loop and need
          fixing.
        </Alert>
      )}

      {distanceWarnings.length > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {distanceWarnings.length} pin(s) sit much further from the
          control room than the OLT reports: {distanceWarnings
            .map((w) => byId[w.node_id]?.label)
            .filter(Boolean)
            .sort()
            .join(', ')}. Likely placed in the wrong spot.
        </Alert>
      )}

      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1, flexWrap: 'wrap' }}>
        <ToggleButtonGroup size="small" exclusive value={layerKey}
          onChange={(e, v) => v && setLayerKey(v)}>
          {TILE_LAYERS.map((l) => (
            <ToggleButton key={l.key} value={l.key}>{l.name}</ToggleButton>
          ))}
        </ToggleButtonGroup>

        {/* Every write on this page is admin/finance only -- this toggle,
            the draw-from-here/delete buttons below, and marker dragging are
            all gated on the same canEdit so employee/collector see the map
            with nothing that would 403 on click. */}
        {canEdit && (
          <ToggleButton size="small" value="add" selected={addMode} onChange={toggleAddMode}>
            {nodes.length === 0 ? 'Add node (control room)' : 'Add node'}
          </ToggleButton>
        )}
      </Stack>

      {canEdit && addMode && (
        <Alert severity="info" sx={{ mb: 1 }}>
          {nodes.length === 0
            ? 'Click the map to place the control room.'
            : pendingParent
              ? `Click the map to place the next node down the line from "${pendingParent.label}".`
              : 'Select a placed node and click "Draw from here" to continue a run.'}
        </Alert>
      )}

      {/* Leaflet computes its own size from this container (see
          networkMap.css); a failed tile fetch never throws here -- the map
          still renders nodes/spans over the CSS's grey background, since the
          topology is the data and the imagery is only backdrop. */}
      <Paper className="fiber-map-container">
        <MapContainer center={center} zoom={16} scrollWheelZoom
                      className="leaflet-container">
          <TileLayer key={layer.key} url={layer.url}
                     attribution={layer.attribution} maxZoom={layer.maxZoom} />

          {canEdit && <MapClickCapture onMapClick={handleMapClick} />}

          {spans.map((span) => {
            const a = byId[span.parent_node_id];
            const b = byId[span.child_node_id];
            if (!a || !b) return null;
            const style = spanStyle(span);
            return (
              <Polyline key={`${span.parent_node_id}-${span.child_node_id}`}
                positions={[[a.latitude, a.longitude], [b.latitude, b.longitude]]}
                pathOptions={style}>
                <Tooltip sticky>
                  {a.label} &rarr; {b.label}
                  {span.is_fault_boundary ? ' — FAULT HERE' : ''}
                </Tooltip>
              </Polyline>
            );
          })}

          {nodes.map((node) => {
            const status = nodeStatus[node.id] || 'unknown';
            const icon = nodeDivIcon(node.kind, status);
            return (
              <Marker key={node.id} position={[node.latitude, node.longitude]}
                icon={icon} draggable={canEdit}
                eventHandlers={canEdit ? { dragend: (e) => handleDragEnd(node, e) } : undefined}>
                <Tooltip>
                  <strong>{node.label}</strong><br />
                  {node.kind} — {status}
                  {node.onu_mac ? <><br />{node.onu_mac}</> : null}
                </Tooltip>
                {canEdit && (
                  <Popup>
                    <Typography variant="subtitle2">{node.label}</Typography>
                    <Typography variant="caption" display="block">
                      {node.kind} — {status}{node.onu_mac ? ` — ${node.onu_mac}` : ''}
                    </Typography>
                    <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                      <Button size="small" onClick={() => openEditDialog(node)}>
                        Edit
                      </Button>
                      <Button size="small" onClick={() => startDrawFromHere(node)}>
                        Draw from here
                      </Button>
                      <Button size="small" color="error" onClick={() => handleDelete(node)}>
                        Delete
                      </Button>
                    </Stack>
                  </Popup>
                )}
              </Marker>
            );
          })}
        </MapContainer>
      </Paper>

      <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>
        {data.last_result_at
          ? `ONU status from the OLT check at ${formatStamp(data.last_result_at)}.`
          : 'No successful OLT check yet — every span is shown as unknown.'}
      </Typography>

      <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
        <Typography variant="subtitle2">
          Unplaced ONUs ({unplacedFiltered.length})
        </Typography>
        {unplacedFiltered.length === 0 ? (
          <Typography variant="caption" color="text.secondary">
            Every known ONU is on the map.
          </Typography>
        ) : (
          <List dense>
            {unplacedFiltered.map((o) => (
              <ListItem key={o.mac_address} disableGutters>
                <ListItemText
                  primary={o.description || o.mac_address}
                  secondary={
                    `${o.mac_address}${o.pon_port ? ` — port ${o.pon_port}` : ''}` +
                    (o.customers && o.customers.length
                      ? ` — ${o.customers.map((c) => c.name).join(', ')}`
                      : '')
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </Paper>

      {dialog && (
        <Dialog open onClose={closeDialog} fullWidth maxWidth="sm">
          <DialogTitle>
            {dialog.mode === 'edit'
              ? 'Edit node'
              : (dialog.kind === 'root' ? 'Place the control room' : 'Add node')}
          </DialogTitle>
          <DialogContent dividers>
            <Stack spacing={2} sx={{ mt: 1 }}>
              {dialog.kind !== 'root' && (
                <TextField select label="Kind" value={dialog.kind}
                  onChange={(e) => setDialog((d) => ({ ...d, kind: e.target.value, onuMac: '' }))}>
                  <MenuItem value="junction">Junction (pole / splitter)</MenuItem>
                  <MenuItem value="onu">ONU (subscriber)</MenuItem>
                </TextField>
              )}
              <TextField label="Label" value={dialog.label}
                onChange={(e) => setDialog((d) => ({ ...d, label: e.target.value }))} />
              {dialog.kind === 'onu' && (
                <Autocomplete
                  options={onuAutocompleteOptions}
                  isOptionEqualToValue={(o, v) => o.mac_address === v.mac_address}
                  getOptionLabel={(o) =>
                    `${o.mac_address}${o.description ? ` — ${o.description}` : ''}` +
                    (o.customers && o.customers.length
                      ? ` (${o.customers.map((c) => c.name).join(', ')})`
                      : '')}
                  value={dialog.onuMac
                    ? (onuAutocompleteOptions.find((o) => o.mac_address === dialog.onuMac) || null)
                    : null}
                  onChange={(e, val) => setDialog((d) => ({
                    ...d,
                    onuMac: val ? val.mac_address : '',
                    label: d.label || (val && (val.description || val.mac_address)) || '',
                  }))}
                  renderInput={(params) => <TextField {...params} label="Unplaced ONU" />}
                />
              )}
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={closeDialog} disabled={saving}>Cancel</Button>
            <Button variant="contained" onClick={submitDialog} disabled={saving}>
              {saving ? 'Saving…' : (dialog.mode === 'edit' ? 'Save' : 'Place node')}
            </Button>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
}
