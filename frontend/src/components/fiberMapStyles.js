// Pure style rules for the geographic fibre map, kept out of the component so
// they can be tested without mounting Leaflet in jsdom. Mirrors the existing
// buildTopologyTree.js / mergeNetworkStatus.js pattern.

const GREEN = '#2e7d32';
const RED = '#c62828';
const GREY = '#9e9e9e';

// Both sources are keyless and were verified serving real imagery over
// Tripoli. Esri World Imagery is usable to z=19 (~0.3 m/px, enough to pin a
// rooftop); OSM's own maximum is 19 and it 400s above that.
export const TILE_LAYERS = [
  {
    key: 'satellite',
    name: 'Satellite',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    maxZoom: 19,
  },
  {
    key: 'street',
    name: 'Street',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  },
];

export function spanStyle(span) {
  const status = (span && span.status) || 'grey';
  if (status === 'green') {
    return { color: GREEN, weight: 3, dashArray: null, className: '' };
  }
  if (status === 'red') {
    // The fault boundary is the one span worth driving to, so it is the only
    // thing on the map that moves. Everything red below it is consequence.
    return span.is_fault_boundary
      ? { color: RED, weight: 7, dashArray: null,
          className: 'fiber-span-fault-boundary' }
      : { color: RED, weight: 3, dashArray: null, className: '' };
  }
  // Dashed, so "we have no data" is visually distinct from "it is up" at a
  // glance and can never be misread as an outage.
  return { color: GREY, weight: 2, dashArray: '6 6', className: '' };
}

export function nodeMarkerStyle(kind, status) {
  const color = status === 'online' ? GREEN : status === 'offline' ? RED : GREY;
  const radius = kind === 'root' ? 10 : kind === 'junction' ? 6 : 7;
  return { color, radius, fillOpacity: 0.9 };
}
