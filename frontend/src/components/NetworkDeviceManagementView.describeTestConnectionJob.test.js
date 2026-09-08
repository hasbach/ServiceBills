// Plain-function test for describeTestConnectionJob -- the fix for a
// reviewed bug where Test Connection reported `data.ok` (which only means
// _create_device_job accepted the work, not that the test passed) as the
// pass/fail signal. In direct mode the connector has already run inline by
// the time that response comes back, so a wrong password produced a green
// "Connection test queued." toast right next to a row whose chip had just
// flipped to Auth Failed. The fix polls the job and reports job.error
// instead; this test pins that mapping.
//
// Same AppContext mock as NetworkTreeView's plain-function tests
// (NetworkTreeView.describeAge.test.js) and
// SubscriptionsView.shouldShowNetworkStatusChips.test.js:
// NetworkDeviceManagementView.js pulls in '../context/AppContext' (directly,
// and again transitively through pollNetworkJob.js), which pulls in the real
// `axios` package, whose installed build ships an ESM-only index.js that
// this project's plain CRA jest config cannot parse. Mocking it here keeps
// this a true plain-function test without dragging that unrelated
// resolution problem in -- this project has no @testing-library/react, so a
// real render of the Test Connection button (and the click handler that
// calls describeTestConnectionJob) isn't exercised anywhere.
jest.mock('../context/AppContext', () => ({
    apiService: {},
    useAppContext: () => ({ setSnackbar: () => {} }),
}));

import { describeTestConnectionJob } from './NetworkDeviceManagementView';

describe('describeTestConnectionJob', () => {
    test('job succeeded (no error) -- reports Connection OK as success', () => {
        expect(describeTestConnectionJob({ status: 'done', error: null, result: 'Connected successfully.' }))
            .toEqual({ message: 'Connection OK', severity: 'success' });
    });

    test('job finished with an error -- reports the error as a failure, not a queued success', () => {
        // The exact case from the review finding: a wrong password. Before
        // the fix this reached the user as a green "Connection test
        // queued." toast because the caller reported `data.ok` (the job was
        // accepted) instead of `job.error` (what the job actually found).
        expect(describeTestConnectionJob({ status: 'done', error: 'invalid user name or password', result: null }))
            .toEqual({ message: 'invalid user name or password', severity: 'error' });
    });

    test('job never resolved -- pollNetworkJob\'s own timeout error surfaces the same way', () => {
        expect(describeTestConnectionJob({ status: 'failed', error: 'Timed out waiting for the check to finish.', result: null }))
            .toEqual({ message: 'Timed out waiting for the check to finish.', severity: 'error' });
    });
});
