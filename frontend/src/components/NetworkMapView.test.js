import React from 'react';
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import '@testing-library/jest-dom';

// react-leaflet renders a real Leaflet map, which jsdom cannot size. Stub it to
// plain divs: these tests are about which CONTROLS appear for which role and
// what the page does with the payload -- not about Leaflet's own rendering.
//
// The mocks below are ACTIVE, not inert: useMapEvents records the handler map
// it was given (mockMapEventHandlers.current) so a test can fire a `click`
// with a realistic Leaflet event shape ({ latlng: { lat, lng } }); Marker
// records its own `draggable` and `eventHandlers` props, keyed by position, so
// a test can find the marker for a specific node and fire a realistic
// `dragend` event ({ target: { getLatLng: () => ({ lat, lng }) } }) -- exactly
// what NetworkMapView.js's handleDragEnd reads off the event.
const mockMapEventHandlers = { current: null };
const mockMarkersByPosition = new Map();
// FINDING 8 (final whole-branch review): the mocks used to discard
// `pathOptions` (Polyline) and `icon` (Marker) entirely, which is exactly
// why deleting `pathOptions={style}` from the Polyline, or hardcoding
// nodeDivIcon(node.kind, 'online'), left all 16 tests in this file green --
// nothing recorded either prop, so nothing could assert on it. Both mocks
// now record them the same way `draggable`/`eventHandlers` already were.
const mockSpansByPositions = new Map();
jest.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map">{children}</div>,
  TileLayer: ({ url }) => <div data-testid="tile" data-url={url} />,
  CircleMarker: ({ children }) => <div data-testid="marker">{children}</div>,
  Marker: ({ children, position, draggable, eventHandlers, icon }) => {
    const key = position.join(',');
    mockMarkersByPosition.set(key, { draggable: !!draggable, eventHandlers, icon });
    return (
      <div data-testid="marker" data-position={key}>
        {children}
      </div>
    );
  },
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  Polyline: ({ children, positions, pathOptions }) => {
    const key = positions.map((p) => p.join(',')).join('|');
    mockSpansByPositions.set(key, { pathOptions });
    return (
      <div data-testid="span" data-positions={key}>
        {children}
      </div>
    );
  },
  Tooltip: ({ children }) => <div>{children}</div>,
  useMapEvents: (handlers) => {
    mockMapEventHandlers.current = handlers;
    return null;
  },
}));

// FINDING 1: the OLT check timestamp must be routed through formatStamp
// rather than printed as the raw UTC string the API sends -- see
// formatStamp.js's own docstring for the measured "3 hours behind" symptom.
// Mocked (not just exercised) so the assertion is that the component calls
// it with the raw stamp and renders its return value, not merely that some
// unspecified transformation happened to occur.
jest.mock('./formatStamp', () => ({
  formatStamp: jest.fn(() => 'FORMATTED_STAMP'),
}));

// apiService has no generic `.get` -- the raw axios instance lives at
// apiService.api (see NetworkMapView.js's own comment on this, and
// ServiceManagementView.js for the same convention). Mocking a bare
// `apiService.get` -- as an earlier sketch of this test did -- would pass
// against a shape the app never actually uses.
const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
const mockApiPut = jest.fn();
const mockApiDelete = jest.fn();
const mockSetSnackbar = jest.fn();
jest.mock('../context/AppContext', () => ({
  apiService: {
    api: {
      get: (...a) => mockApiGet(...a),
      post: (...a) => mockApiPost(...a),
      put: (...a) => mockApiPut(...a),
      delete: (...a) => mockApiDelete(...a),
    },
  },
  useAppContext: () => ({ setSnackbar: mockSetSnackbar }),
}));

import NetworkMapView from './NetworkMapView';
import { formatStamp } from './formatStamp';
import { spanStyle, nodeMarkerStyle } from './fiberMapStyles';

const PAYLOAD = {
  nodes: [
    { id: 1, kind: 'root', label: 'Control Room', latitude: 34.4367,
      longitude: 35.8497, parent_node_id: null, onu_mac: null },
    { id: 2, kind: 'onu', label: 'Villa Eid', latitude: 34.4368,
      longitude: 35.8498, parent_node_id: 1, onu_mac: 'aa:aa:aa:aa:aa:aa' },
  ],
  spans: [{ parent_node_id: 1, child_node_id: 2, status: 'red',
            is_fault_boundary: true }],
  node_status: { 1: 'online', 2: 'offline' },
  orphans: [],
  onu_status: { aaaaaaaaaaaa: 'offline' },
  last_result_at: '2026-09-10 19:00:00',
  distance_warnings: [],
};

// FINDING 8 fixture: one red fault-boundary span, one green span, and one
// ONU with no span at all (status 'unknown') -- enough distinct colours to
// prove the style helpers are actually wired to what gets rendered, not
// just correct in isolation (fiberMapStyles.test.js already covers that).
const STYLE_PAYLOAD = {
  nodes: [
    { id: 1, kind: 'root', label: 'Control Room', latitude: 34.4367,
      longitude: 35.8497, parent_node_id: null, onu_mac: null },
    { id: 2, kind: 'onu', label: 'Villa Eid', latitude: 34.4368,
      longitude: 35.8498, parent_node_id: 1, onu_mac: 'aa:aa:aa:aa:aa:aa' },
    { id: 3, kind: 'onu', label: 'Villa Khoury', latitude: 34.4369,
      longitude: 35.8499, parent_node_id: 1, onu_mac: 'bb:bb:bb:bb:bb:bb' },
    { id: 4, kind: 'onu', label: 'Villa Nassar', latitude: 34.4370,
      longitude: 35.8500, parent_node_id: 1, onu_mac: null },
  ],
  spans: [
    { parent_node_id: 1, child_node_id: 2, status: 'red', is_fault_boundary: true },
    { parent_node_id: 1, child_node_id: 3, status: 'green', is_fault_boundary: false },
  ],
  node_status: { 1: 'online', 2: 'offline', 3: 'online', 4: 'unknown' },
  orphans: [],
  onu_status: { 'aa:aa:aa:aa:aa:aa': 'offline', 'bb:bb:bb:bb:bb:bb': 'online' },
  last_result_at: '2026-09-10 19:00:00',
  distance_warnings: [],
};

const UNPLACED = {
  onus: [
    { mac_address: 'bb:bb:bb:bb:bb:bb', pon_port: '1/1', onu_id: 3,
      description: 'ONU-3', status: 'online', customers: [] },
    { mac_address: 'aa:aa:aa:aa:aa:aa', pon_port: '1/1', onu_id: 2,
      description: 'ONU-2 (placed)', status: 'offline', customers: [] },
  ],
};

// Finds the mock Marker DOM node whose children render the given text (e.g. a
// node's label, shown in its Tooltip/Popup), then looks up the real `Marker`
// props (draggable / eventHandlers) that mock recorded for that position.
function markerPropsFor(text) {
  const el = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(text).length > 0);
  if (!el) return null;
  return mockMarkersByPosition.get(el.getAttribute('data-position'));
}

// Same idea as markerPropsFor, but for the Polyline mock: finds the span
// whose Tooltip text matches (e.g. a node label it connects to), then looks
// up the real `pathOptions` prop that mock recorded for that span.
function spanPropsFor(text) {
  const el = screen.getAllByTestId('span')
    .find((node) => within(node).queryAllByText(text).length > 0);
  if (!el) return null;
  return mockSpansByPositions.get(el.getAttribute('data-positions'));
}

// Duplicated on purpose rather than imported: NetworkMapView.js's own
// hexToRgba is what bakes nodeMarkerStyle's color into the divIcon html, and
// recomputing it independently here means this test still catches a broken
// wiring even if that helper itself were changed.
function hexToRgbaForTest(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

beforeEach(() => {
  mockApiGet.mockReset();
  mockApiPost.mockReset();
  mockApiPut.mockReset();
  mockApiDelete.mockReset();
  mockSetSnackbar.mockReset();
  // CRA's default jest config sets resetMocks: true, which strips every
  // jest.fn()'s implementation before each test (see NetworkMapPage.test.js's
  // own note on this) -- so formatStamp's stub return value has to be
  // re-supplied here, not just once at the jest.mock() factory.
  formatStamp.mockImplementation(() => 'FORMATTED_STAMP');
  mockMapEventHandlers.current = null;
  mockMarkersByPosition.clear();
  mockSpansByPositions.clear();
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.resolve({ data: PAYLOAD });
  });
});

test('names the span the fault was localised to', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getAllByText(/Villa Eid/).length).toBeGreaterThan(0);
  expect(screen.getByText(/Fault localised/)).toBeInTheDocument();
});

test('renders the map for an employee but offers no editing controls', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="employee" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: /add node/i })).toBeNull();
  expect(screen.queryByRole('button', { name: /draw from here/i })).toBeNull();
});

test('renders the map for a collector but offers no editing controls', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="collector" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: /add node/i })).toBeNull();
  expect(screen.queryByRole('button', { name: /draw from here/i })).toBeNull();
  expect(screen.queryByRole('button', { name: /^delete$/i })).toBeNull();
});

test('offers editing controls to an admin', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getByRole('button', { name: /add node/i })).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: /draw from here/i }).length)
    .toBeGreaterThan(0);
});

test('offers editing controls to finance too', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="finance" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getByRole('button', { name: /add node/i })).toBeInTheDocument();
});

test('a failed load shows an error and never an empty map', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.reject(new Error('boom'));
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() =>
    expect(screen.getByText(/network map is unavailable/i)).toBeInTheDocument());
  expect(screen.queryByTestId('map')).toBeNull();
});

test('says so plainly when no OLT check has ever succeeded', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.resolve({
      data: { ...PAYLOAD, last_result_at: null, spans: [], node_status: {} } });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() =>
    expect(screen.getByText(/No successful OLT check yet/i)).toBeInTheDocument());
});

test('warns about orphaned nodes rather than hiding them silently', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.resolve({ data: { ...PAYLOAD, orphans: [7, 8] } });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() =>
    expect(screen.getByText(/2 node\(s\) are not connected/i)).toBeInTheDocument());
});

test('FINDING 3: an orphaned node is still drawn as a marker -- only its span is suppressed', async () => {
  // Node 2 ("Villa Eid") is marked orphaned here, and its span is dropped
  // entirely -- but the node itself must still render as a marker. The old
  // banner claimed orphaned nodes "are not drawn", which was false: only
  // their spans are suppressed (see NetworkMapView.js's data.nodes.map,
  // which renders a marker for every node with no orphan exclusion).
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.resolve({ data: { ...PAYLOAD, orphans: [2], spans: [] } });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getAllByText(/Villa Eid/).length).toBeGreaterThan(0);
  expect(screen.getByText(/their spans are not drawn/i)).toBeInTheDocument();
  expect(screen.queryByText(/are not connected to the control room\s*and are not drawn/i))
    .toBeNull();
});

test('shows the unplaced-ONU panel, excluding ONUs already placed on the map', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  // Both rows come back from the (mocked) unplaced-onus endpoint, but
  // aa:aa:aa:aa:aa:aa is already placed as node 2 in PAYLOAD -- the
  // component itself must filter it back out of the panel rather than
  // trusting the endpoint to only ever return what's still unplaced.
  // (aa:aa:aa:aa:aa:aa legitimately still appears elsewhere on the page --
  // it's node 2's own MAC, shown on its marker -- so this scopes the
  // assertion to the panel itself rather than the whole document.)
  await waitFor(() => expect(screen.getByText(/bb:bb:bb:bb:bb:bb/)).toBeInTheDocument());
  const heading = screen.getByText(/Unplaced ONUs/);
  const panel = heading.closest('.MuiPaper-root') || heading.parentElement;
  expect(within(panel).queryByText(/aa:aa:aa:aa:aa:aa/)).toBeNull();
  expect(within(panel).getByText(/bb:bb:bb:bb:bb:bb/)).toBeInTheDocument();
});

test('a successful write reloads the map without ever blanking it first', async () => {
  // The first /network-map GET (initial load) resolves immediately; the
  // second (the post-write reload) is held open under our control so we can
  // inspect the DOM while the reload is still in flight -- that's the only
  // moment a "clear data to null before refetching" bug would be visible.
  let resolveReload;
  let networkMapCalls = 0;
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    networkMapCalls += 1;
    if (networkMapCalls === 1) return Promise.resolve({ data: PAYLOAD });
    return new Promise((resolve) => { resolveReload = () => resolve({ data: PAYLOAD }); });
  });
  mockApiDelete.mockResolvedValue({ data: { message: 'Node deleted' } });

  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getAllByText(/Villa Eid/).length).toBeGreaterThan(0);

  const deleteButtons = screen.getAllByRole('button', { name: /^delete$/i });
  await act(async () => { fireEvent.click(deleteButtons[deleteButtons.length - 1]); });

  // The reload triggered by the successful delete is still pending here
  // (resolveReload has not been called yet). The map and its existing
  // content must still be on screen -- not cleared to null while waiting.
  await waitFor(() => expect(resolveReload).toBeDefined());
  expect(screen.getByTestId('map')).toBeInTheDocument();
  expect(screen.getAllByText(/Villa Eid/).length).toBeGreaterThan(0);

  await act(async () => { resolveReload(); });
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.getAllByText(/Villa Eid/).length).toBeGreaterThan(0);
});

test('a 409 on delete surfaces the server message verbatim', async () => {
  mockApiDelete.mockRejectedValue({
    response: { status: 409, data: { message: 'Cannot delete "Control Room" -- it still carries: Villa Eid.' } },
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const deleteButtons = screen.getAllByRole('button', { name: /^delete$/i });
  await act(async () => { fireEvent.click(deleteButtons[0]); });

  await waitFor(() => expect(mockSetSnackbar).toHaveBeenCalledWith(
    expect.objectContaining({
      severity: 'error',
      message: 'Cannot delete "Control Room" -- it still carries: Villa Eid.',
    })));
  // The map must still be showing (delete failed, nothing should vanish).
  expect(screen.getAllByText(/Villa Eid/).length).toBeGreaterThan(0);
});

test('clicking the map in add-mode as an admin begins placing the control room', async () => {
  // Zero nodes yet -- the simplest, unambiguous case: a click should
  // immediately open the placement dialog rather than needing a
  // "Draw from here" parent selected first.
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') return Promise.resolve({ data: UNPLACED });
    return Promise.resolve({ data: { ...PAYLOAD, nodes: [], spans: [], orphans: [] } });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /add node/i }));
  });
  expect(mockMapEventHandlers.current).not.toBeNull();

  await act(async () => {
    mockMapEventHandlers.current.click({ latlng: { lat: 34.44, lng: 35.85 } });
  });

  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText(/Place the control room/i)).toBeInTheDocument();
  expect(mockApiPost).not.toHaveBeenCalled();
});

test('clicking the map as an employee does nothing -- the canEdit gate', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="employee" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  // MapClickCapture (and the useMapEvents click handler it wires up) is only
  // ever mounted when canEdit is true -- an employee should never even
  // register a click handler in the first place.
  expect(mockMapEventHandlers.current).toBeNull();

  // Belt and braces: even if a handler somehow got registered, firing it
  // must be a no-op -- no dialog, no POST.
  if (mockMapEventHandlers.current) {
    act(() => { mockMapEventHandlers.current.click({ latlng: { lat: 34.44, lng: 35.85 } }); });
  }
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(mockApiPost).not.toHaveBeenCalled();
});

test('dragging a marker as an admin issues a PUT carrying the new coordinates', async () => {
  mockApiPut.mockResolvedValue({ data: {} });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const { eventHandlers } = markerPropsFor(/Villa Eid/);
  expect(typeof eventHandlers?.dragend).toBe('function');

  await act(async () => {
    eventHandlers.dragend({ target: { getLatLng: () => ({ lat: 34.5, lng: 35.9 }) } });
  });

  expect(mockApiPut).toHaveBeenCalledWith(
    '/network-map/nodes/2', { latitude: 34.5, longitude: 35.9 });
});

test('a failed drag PUT reloads the map so the pin snaps back, and tells the user', async () => {
  mockApiPut.mockRejectedValue(new Error('boom'));
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const mapCallsBefore = mockApiGet.mock.calls
    .filter((c) => c[0] !== '/network-map/unplaced-onus').length;

  const { eventHandlers } = markerPropsFor(/Villa Eid/);
  await act(async () => {
    eventHandlers.dragend({ target: { getLatLng: () => ({ lat: 34.5, lng: 35.9 }) } });
  });

  await waitFor(() => expect(mockSetSnackbar).toHaveBeenCalledWith(
    expect.objectContaining({
      severity: 'error',
      message: expect.stringMatching(/could not move the node/i),
    })));

  const mapCallsAfter = mockApiGet.mock.calls
    .filter((c) => c[0] !== '/network-map/unplaced-onus').length;
  expect(mapCallsAfter).toBeGreaterThan(mapCallsBefore);
});

test('markers are draggable for an admin and not for an employee', async () => {
  const admin = render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(markerPropsFor(/Villa Eid/).draggable).toBe(true);
  admin.unmount();

  mockMarkersByPosition.clear();
  render(<NetworkMapView oltDeviceId={1} userRole="employee" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(markerPropsFor(/Villa Eid/).draggable).toBe(false);
});

test('FINDING 1: routes the OLT check timestamp through formatStamp instead of the raw UTC string', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  // PAYLOAD.last_result_at ('2026-09-10 19:00:00') is naked UTC with no zone
  // marker -- formatStamp.js's own docstring records the measured symptom of
  // printing that verbatim (reads hours behind for this ISP). The component
  // must call formatStamp with the raw stamp and render ITS return value.
  expect(formatStamp).toHaveBeenCalledWith(PAYLOAD.last_result_at);
  expect(screen.getByText(/ONU status from the OLT check at FORMATTED_STAMP\./))
    .toBeInTheDocument();
  expect(screen.queryByText(/OLT check at 2026-09-10 19:00:00/)).toBeNull();
});

test('FINDING 2: a bare-string payload (the offline service-worker fallback) does not throw', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    // frontend/public/service-worker.js's known defect: any failed fetch
    // resolves to a fake 200 whose body is this literal string. It is
    // truthy, so it passes the `if (!data)` guard and previously reached
    // data.nodes.map (etc.) completely unguarded, throwing with no React
    // error boundary to catch it -- white-screening the whole app.
    return Promise.resolve({ data: 'You are offline.' });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryAllByTestId('marker').length).toBe(0);
  expect(screen.queryAllByTestId('span').length).toBe(0);
});

test('FINDING 2: a payload object missing expected keys does not throw', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.resolve({ data: {} });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryAllByTestId('marker').length).toBe(0);
  expect(screen.queryAllByTestId('span').length).toBe(0);
  expect(screen.getByText(/No successful OLT check yet/i)).toBeInTheDocument();
});

test('FINDING 4: a distance warning names the pins it flags, not just a bare count', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: UNPLACED });
    }
    return Promise.resolve({ data: { ...PAYLOAD,
      distance_warnings: [{ node_id: 2, chain_metres: 900, reported_metres: 100 }] } });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const distanceAlert = Array.from(document.querySelectorAll('.MuiAlert-message'))
    .find((el) => /pin\(s\) sit much further/.test(el.textContent));
  expect(distanceAlert).toBeTruthy();
  // Node 2's label ("Villa Eid") must be named in the alert -- staff cannot
  // act on "2 pins look wrong" with no way to find which pins those are.
  expect(distanceAlert.textContent).toMatch(/Villa Eid/);
});

test('FINDING 5: a MAC placed with one separator style is recognised when reported with another', async () => {
  // Node 2 in PAYLOAD stores its onu_mac colon-separated
  // ('aa:aa:aa:aa:aa:aa'). Report that same ONU back from the
  // unplaced-onus endpoint in a completely different separator style and
  // case -- hyphens, upper case -- to prove the panel's own defence-in-depth
  // re-filter normalises separators the same way the backend's
  // _normalize_mac does, not just case via bare .toLowerCase().
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') {
      return Promise.resolve({ data: { onus: [
        { mac_address: 'AA-AA-AA-AA-AA-AA', pon_port: '1/1', onu_id: 2,
          description: 'ONU-2 (placed)', status: 'offline', customers: [] },
      ] } });
    }
    return Promise.resolve({ data: PAYLOAD });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  const heading = screen.getByText(/Unplaced ONUs/);
  const panel = heading.closest('.MuiPaper-root') || heading.parentElement;
  await waitFor(() => expect(within(panel).getByText(/Every known ONU is on the map\./i))
    .toBeInTheDocument());
});

test('FINDING 8: a red span gets the red stroke, and the fault boundary gets the heavier weight and its animation class', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') return Promise.resolve({ data: UNPLACED });
    return Promise.resolve({ data: STYLE_PAYLOAD });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const faultSpan = spanPropsFor(/Villa Eid/);
  const greenSpan = spanPropsFor(/Villa Khoury/);
  expect(faultSpan?.pathOptions).toEqual(spanStyle({ status: 'red', is_fault_boundary: true }));
  expect(greenSpan?.pathOptions).toEqual(spanStyle({ status: 'green', is_fault_boundary: false }));

  // The fault boundary is "the one span worth driving to" -- it must stand
  // out from an ordinary green span with both a heavier stroke and its own
  // animation class.
  expect(faultSpan.pathOptions.weight).toBeGreaterThan(greenSpan.pathOptions.weight);
  expect(faultSpan.pathOptions.className).toBe('fiber-span-fault-boundary');
  expect(greenSpan.pathOptions.className).not.toBe('fiber-span-fault-boundary');
});

test('FINDING 8: a grey/unknown node is not rendered red', async () => {
  mockApiGet.mockImplementation((url) => {
    if (url === '/network-map/unplaced-onus') return Promise.resolve({ data: UNPLACED });
    return Promise.resolve({ data: STYLE_PAYLOAD });
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  // Node 4 ("Villa Nassar") has no span and node_status 'unknown'.
  const unknownMarker = markerPropsFor(/Villa Nassar/);
  const redStyle = nodeMarkerStyle('onu', 'offline');
  const greyStyle = nodeMarkerStyle('onu', 'unknown');
  const html = unknownMarker?.icon?.options?.html || '';
  expect(html).toContain(hexToRgbaForTest(greyStyle.color, greyStyle.fillOpacity));
  expect(html).not.toContain(hexToRgbaForTest(redStyle.color, redStyle.fillOpacity));
});

test('clicking Edit on a node opens the dialog pre-filled with its current kind/label/onu_mac', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });

  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByText(/edit node/i)).toBeInTheDocument();
  expect(within(dialog).getByDisplayValue('Villa Eid')).toBeInTheDocument();
});

test('an employee sees no Edit control on a node', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="employee" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: /^edit$/i })).toBeNull();
});

test('saving an edit PUTs exactly {kind, label, onu_mac} with no coordinates or parent', async () => {
  mockApiPut.mockResolvedValue({ data: {} });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));
  });

  expect(mockApiPut).toHaveBeenCalledWith('/network-map/nodes/2', {
    kind: 'onu', label: 'Villa Eid', onu_mac: 'aa:aa:aa:aa:aa:aa',
  });
});

test('editing a node from onu to junction sends onu_mac: null', async () => {
  mockApiPut.mockResolvedValue({ data: {} });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  await act(async () => {
    fireEvent.mouseDown(within(dialog).getByLabelText(/kind/i));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('option', { name: /junction/i }));
  });
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));
  });

  expect(mockApiPut).toHaveBeenCalledWith('/network-map/nodes/2', {
    kind: 'junction', label: 'Villa Eid', onu_mac: null,
  });
});

test('a validation error on save surfaces the exact backend message', async () => {
  mockApiPut.mockRejectedValue({
    response: { status: 400, data: { message: 'this OLT already has a root node' } },
  });
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  await act(async () => {
    fireEvent.click(within(dialog).getByRole('button', { name: /^save$/i }));
  });

  await waitFor(() => expect(mockSetSnackbar).toHaveBeenCalledWith(
    expect.objectContaining({
      severity: 'error',
      message: 'this OLT already has a root node',
    })));
});

test('the ONU autocomplete offers the node\'s own current onu_mac when editing it', async () => {
  render(<NetworkMapView oltDeviceId={1} userRole="admin" />);
  await waitFor(() => expect(screen.getByTestId('map')).toBeInTheDocument());

  // Node 2 ("Villa Eid") is onu_mac 'aa:aa:aa:aa:aa:aa', which is NOT in
  // UNPLACED (it's already placed, on itself) -- proving the synthetic
  // option is what makes it show up here, not the unplaced list.
  const marker = screen.getAllByTestId('marker')
    .find((node) => within(node).queryAllByText(/Villa Eid/).length > 0);
  await act(async () => {
    fireEvent.click(within(marker).getByRole('button', { name: /^edit$/i }));
  });
  const dialog = screen.getByRole('dialog');
  expect(within(dialog).getByDisplayValue(/aa:aa:aa:aa:aa:aa/i)).toBeInTheDocument();
});
