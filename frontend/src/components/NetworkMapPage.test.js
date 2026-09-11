import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

// NetworkMapPage's whole job is deciding what oltDeviceId/userRole to hand to
// NetworkMapView -- stub the real map component so these tests are about that
// selection logic, not Leaflet or the map's own behaviour (already covered by
// NetworkMapView.test.js).
const mockNetworkMapView = jest.fn(() => <div data-testid="network-map-view" />);
jest.mock('./NetworkMapView', () => (props) => mockNetworkMapView(props));

const mockFetchNetworkDevices = jest.fn();
const mockSetSnackbar = jest.fn();
let mockUser = { role: 'admin' };
jest.mock('../context/AppContext', () => ({
  apiService: {
    fetchNetworkMapOlts: (...a) => mockFetchNetworkDevices(...a),
    fetchNetworkDevices: (...a) => mockFetchNetworkDevices(...a),
  },
  useAppContext: () => ({ user: mockUser, setSnackbar: mockSetSnackbar }),
}));

import NetworkMapPage from './NetworkMapPage';

const OLT_1 = { id: 5, name: 'OLT Koura', device_type: 'vsol_olt' };
const OLT_2 = { id: 9, name: 'OLT Tripoli', device_type: 'vsol_olt' };
const CCR = { id: 2, name: 'Edge CCR', device_type: 'mikrotik_ccr' };

beforeEach(() => {
  // CRA's default jest config sets resetMocks: true, which strips every
  // jest.fn()'s implementation before each test -- so the mock component's
  // render body has to be re-supplied here, not just once at module load.
  mockNetworkMapView.mockImplementation(() => <div data-testid="network-map-view" />);
  mockFetchNetworkDevices.mockReset();
  mockSetSnackbar.mockReset();
  mockUser = { role: 'admin' };
});

test('exactly one OLT is selected automatically with no interaction', async () => {
  mockFetchNetworkDevices.mockResolvedValue({ data: [CCR, OLT_1] });
  render(<NetworkMapPage />);

  await waitFor(() => expect(screen.getByTestId('network-map-view')).toBeInTheDocument());
  expect(mockNetworkMapView).toHaveBeenLastCalledWith(
    expect.objectContaining({ oltDeviceId: OLT_1.id }));
  // No selector should be rendered -- there's nothing to choose.
  expect(screen.queryByLabelText(/olt device/i)).toBeNull();
});

test('zero OLTs renders an actionable empty state, not a map', async () => {
  mockFetchNetworkDevices.mockResolvedValue({ data: [CCR] });
  render(<NetworkMapPage />);

  await waitFor(() => expect(screen.getByText(/no olt device is configured/i)).toBeInTheDocument());
  expect(screen.queryByTestId('network-map-view')).toBeNull();
  expect(mockNetworkMapView).not.toHaveBeenCalled();
});

test('the role reaching NetworkMapView is the real current user\'s role', async () => {
  mockUser = { role: 'employee' };
  mockFetchNetworkDevices.mockResolvedValue({ data: [OLT_1] });
  render(<NetworkMapPage />);

  await waitFor(() => expect(screen.getByTestId('network-map-view')).toBeInTheDocument());
  expect(mockNetworkMapView).toHaveBeenLastCalledWith(
    expect.objectContaining({ userRole: 'employee' }));
});

test('a combined role string resolves to the highest-privilege role', async () => {
  mockUser = { role: 'employee,admin' };
  mockFetchNetworkDevices.mockResolvedValue({ data: [OLT_1] });
  render(<NetworkMapPage />);

  await waitFor(() => expect(screen.getByTestId('network-map-view')).toBeInTheDocument());
  expect(mockNetworkMapView).toHaveBeenLastCalledWith(
    expect.objectContaining({ userRole: 'admin' }));
});

test('more than one OLT offers a select, and the map follows the choice', async () => {
  mockFetchNetworkDevices.mockResolvedValue({ data: [OLT_1, OLT_2] });
  render(<NetworkMapPage />);

  await waitFor(() => expect(screen.getByTestId('network-map-view')).toBeInTheDocument());
  // Defaults to the first OLT without requiring interaction either.
  expect(mockNetworkMapView).toHaveBeenLastCalledWith(
    expect.objectContaining({ oltDeviceId: OLT_1.id }));

  // MUI's Select renders a combobox div, not a native <select> -- opening it
  // and clicking the option is the standard way to drive it, since firing a
  // plain "change" event on the div (as you would a real <select>) is a no-op.
  fireEvent.mouseDown(screen.getByRole('combobox'));
  fireEvent.click(await screen.findByRole('option', { name: OLT_2.name }));

  await waitFor(() => expect(mockNetworkMapView).toHaveBeenLastCalledWith(
    expect.objectContaining({ oltDeviceId: OLT_2.id })));
});

test('a failed device fetch shows an error rather than an endless spinner', async () => {
  mockFetchNetworkDevices.mockRejectedValue(new Error('boom'));
  render(<NetworkMapPage />);

  await waitFor(() => expect(mockSetSnackbar).toHaveBeenCalledWith(
    expect.objectContaining({ severity: 'error' })));
  expect(screen.queryByTestId('network-map-view')).toBeNull();
  expect(screen.getByText(/could not load network devices/i)).toBeInTheDocument();
});

test('switching OLTs remounts the map to reset its internal state', async () => {
  // Track mounts via a useEffect that fires only on component mount.
  // If key={selectedOltId} is present, changing the key unmounts and remounts
  // the component, so this spy is called twice. Without the key, the same
  // component re-renders with new props, so the spy is called only once.
  const mountSpy = jest.fn();
  mockNetworkMapView.mockImplementation((props) => {
    React.useEffect(() => {
      mountSpy(props.oltDeviceId);
    }, []); // Empty deps: runs only on mount, never again for this instance
    return <div data-testid="network-map-view" />;
  });

  mockFetchNetworkDevices.mockResolvedValue({ data: [OLT_1, OLT_2] });
  render(<NetworkMapPage />);

  // Initial render with OLT_1
  await waitFor(() => expect(screen.getByTestId('network-map-view')).toBeInTheDocument());
  expect(mountSpy).toHaveBeenCalledTimes(1);
  expect(mountSpy).toHaveBeenLastCalledWith(OLT_1.id);

  // Switch to OLT_2 via the selector
  fireEvent.mouseDown(screen.getByRole('combobox'));
  fireEvent.click(await screen.findByRole('option', { name: OLT_2.name }));

  // The map should remount (new key), so the mount spy is called a second time.
  // Without key={selectedOltId}, this assertion fails because the component
  // re-renders in place without a new mount.
  await waitFor(() => expect(mountSpy).toHaveBeenCalledTimes(2));
  expect(mountSpy).toHaveBeenLastCalledWith(OLT_2.id);
});
