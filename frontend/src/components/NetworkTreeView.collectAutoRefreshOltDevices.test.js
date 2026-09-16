// Plain-function tests for collectAutoRefreshOltDevices -- the periodic
// auto-refresh timer's device-selection logic. See NetworkTreeView.js and
// docs/superpowers/specs/2026-09-16-network-tree-periodic-auto-refresh-design.md
// for the full design.
//
// Same AppContext mock as NetworkTreeView.toggleExpansion.test.js -- see
// that file's header comment for why (this project has no
// @testing-library/react, and the real axios package's ESM-only build
// can't be parsed by this project's plain CRA jest config).
jest.mock('../context/AppContext', () => ({
    apiService: {},
    useAppContext: () => ({ setSnackbar: () => {}, user: null }),
}));

import { collectAutoRefreshOltDevices } from './NetworkTreeView';

describe('collectAutoRefreshOltDevices', () => {
    it('returns [] for an empty tree', () => {
        expect(collectAutoRefreshOltDevices([], {})).toEqual([]);
    });

    it('treats a missing/undefined tree as empty', () => {
        expect(collectAutoRefreshOltDevices(undefined, {})).toEqual([]);
    });

    it('returns an OLT device at the root', () => {
        const olt = { id: 1, device_type: 'vsol_olt', children: [] };
        expect(collectAutoRefreshOltDevices([olt], {})).toEqual([olt]);
    });

    it('excludes a non-OLT device', () => {
        const ccr = { id: 2, device_type: 'mikrotik_ccr', children: [] };
        expect(collectAutoRefreshOltDevices([ccr], {})).toEqual([]);
    });

    it('excludes an OLT device currently mid-refresh', () => {
        const olt = { id: 1, device_type: 'vsol_olt', children: [] };
        expect(collectAutoRefreshOltDevices([olt], { 1: true })).toEqual([]);
    });

    it('finds an OLT nested under a non-OLT root', () => {
        const olt = { id: 3, device_type: 'vsol_olt', children: [] };
        const ccr = { id: 2, device_type: 'mikrotik_ccr', children: [olt] };
        expect(collectAutoRefreshOltDevices([ccr], {})).toEqual([olt]);
    });

    it('collects OLTs across multiple root trees', () => {
        const oltA = { id: 1, device_type: 'vsol_olt', children: [] };
        const oltB = { id: 2, device_type: 'vsol_olt', children: [] };
        expect(collectAutoRefreshOltDevices([oltA, oltB], {})).toEqual([oltA, oltB]);
    });
});
