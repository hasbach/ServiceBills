import React from 'react';
import { render, screen, waitFor, fireEvent, act, within } from '@testing-library/react';
import '@testing-library/jest-dom';

// react-leaflet renders a real Leaflet map, which jsdom cannot size. Stub it to
// plain divs: these tests are about which CONTROLS appear for which role and
// what the page does with the payload -- not about Leaflet's own rendering.
// useMapEvents is stubbed to a no-op (rather than omitted) so the component's
// click-capture child can call it without crashing; no test here simulates an
// actual map click.
jest.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="map">{children}</div>,
  TileLayer: ({ url }) => <div data-testid="tile" data-url={url} />,
  CircleMarker: ({ children }) => <div data-testid="marker">{children}</div>,
  Marker: ({ children }) => <div data-testid="marker">{children}</div>,
  Popup: ({ children }) => <div data-testid="popup">{children}</div>,
  Polyline: ({ children }) => <div data-testid="span">{children}</div>,
  Tooltip: ({ children }) => <div>{children}</div>,
  useMapEvents: () => null,
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

const UNPLACED = {
  onus: [
    { mac_address: 'bb:bb:bb:bb:bb:bb', pon_port: '1/1', onu_id: 3,
      description: 'ONU-3', status: 'online', customers: [] },
    { mac_address: 'aa:aa:aa:aa:aa:aa', pon_port: '1/1', onu_id: 2,
      description: 'ONU-2 (placed)', status: 'offline', customers: [] },
  ],
};

beforeEach(() => {
  mockApiGet.mockReset();
  mockApiPost.mockReset();
  mockApiPut.mockReset();
  mockApiDelete.mockReset();
  mockSetSnackbar.mockReset();
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
