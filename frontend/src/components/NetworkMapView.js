import React, { useCallback, useEffect, useState } from 'react';
import { Box, Typography, Alert, CircularProgress, ToggleButton,
         ToggleButtonGroup, Paper } from '@mui/material';
import { MapContainer, TileLayer, CircleMarker, Polyline, Tooltip } from 'react-leaflet';
// apiService is a direct module export of AppContext.js, not part of the
// hook's value -- same convention as NetworkTreeView.js/
// NetworkDeviceManagementView.js. There is no generic apiService.get(); the
// raw axios instance lives at apiService.api (see ServiceManagementView.js
// for the same apiService.api.get(...) pattern used for an endpoint that
// has no dedicated named method yet), and its baseURL already carries
// '/api', so paths here start at '/network-map', not '/api/network-map'.
import { apiService, useAppContext } from '../context/AppContext';
import { spanStyle, nodeMarkerStyle, TILE_LAYERS } from './fiberMapStyles';
import 'leaflet/dist/leaflet.css';
import './networkMap.css';

// Tripoli / Koura -- where DeltaNet's plant is. Only used when a tenant has no
// nodes placed yet; once a root exists the map centres on it.
const DEFAULT_CENTER = [34.4367, 35.8497];

export default function NetworkMapView({ oltDeviceId }) {
  const { setSnackbar } = useAppContext();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [layerKey, setLayerKey] = useState('satellite');

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

  useEffect(() => { load(); }, [load]);

  if (loading) return <CircularProgress />;
  if (!data) return <Alert severity="error">The network map is unavailable.</Alert>;

  const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
  const root = data.nodes.find((n) => n.kind === 'root');
  const center = root ? [root.latitude, root.longitude] : DEFAULT_CENTER;
  const layer = TILE_LAYERS.find((l) => l.key === layerKey) || TILE_LAYERS[0];
  const boundary = data.spans.filter((s) => s.is_fault_boundary);

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

      {data.orphans.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {data.orphans.length} node(s) are not connected to the control room
          and are not drawn. Their parent links form a loop and need fixing.
        </Alert>
      )}

      {data.distance_warnings.length > 0 && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {data.distance_warnings.length} pin(s) sit much further from the
          control room than the OLT reports. Likely placed in the wrong spot.
        </Alert>
      )}

      <ToggleButtonGroup size="small" exclusive value={layerKey} sx={{ mb: 1 }}
        onChange={(e, v) => v && setLayerKey(v)}>
        {TILE_LAYERS.map((l) => (
          <ToggleButton key={l.key} value={l.key}>{l.name}</ToggleButton>
        ))}
      </ToggleButtonGroup>

      {/* Leaflet computes its own size from this container (see
          networkMap.css); a failed tile fetch never throws here -- the map
          still renders nodes/spans over the CSS's grey background, since the
          topology is the data and the imagery is only backdrop. */}
      <Paper className="fiber-map-container">
        <MapContainer center={center} zoom={16} scrollWheelZoom
                      className="leaflet-container">
          <TileLayer key={layer.key} url={layer.url}
                     attribution={layer.attribution} maxZoom={layer.maxZoom} />

          {data.spans.map((span) => {
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

          {data.nodes.map((node) => {
            const status = data.node_status[node.id] || 'unknown';
            const style = nodeMarkerStyle(node.kind, status);
            return (
              <CircleMarker key={node.id}
                center={[node.latitude, node.longitude]}
                radius={style.radius}
                pathOptions={{ color: style.color, fillColor: style.color,
                               fillOpacity: style.fillOpacity }}>
                <Tooltip>
                  <strong>{node.label}</strong><br />
                  {node.kind} — {status}
                  {node.onu_mac ? <><br />{node.onu_mac}</> : null}
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>
      </Paper>

      <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>
        {data.last_result_at
          ? `ONU status from the OLT check at ${data.last_result_at}.`
          : 'No successful OLT check yet — every span is shown as unknown.'}
      </Typography>
    </Box>
  );
}
