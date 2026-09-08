// Plain-function test for shouldShowNetworkStatusChips -- the fix for a
// reviewed bug where the Network Status panel swapped from its "Checking…"
// state straight to the finished chips the moment the FIRST poll came back,
// even when mergeNetworkStatus said the merged result was still `pending`.
// Both the secret_status and active_session jobs have to land before a
// result is final (see mergeNetworkStatus.js); in agent mode, where
// fetchNetworkStatus polls once every ~2 seconds per job, that window is
// real and a bare "Not connected" chip during it reads as a finished answer.
//
// Same AppContext mock as NetworkTreeView's plain-function tests
// (NetworkTreeView.describeAge.test.js / NetworkTreeView.toggleExpansion.test.js):
// SubscriptionsView.js pulls in '../context/AppContext.js', which pulls in
// the real `axios` package, whose installed build ships an ESM-only index.js
// that this project's plain CRA jest config cannot parse. Mocking it here
// keeps this a true plain-function test without dragging that unrelated
// resolution problem in -- this project has no @testing-library/react, so a
// real render of the panel isn't exercised anywhere.
jest.mock('../context/AppContext.js', () => ({
    apiService: {},
    useAppContext: () => ({ setSnackbar: () => {}, user: null }),
}));

import { shouldShowNetworkStatusChips } from './SubscriptionsView';

describe('shouldShowNetworkStatusChips', () => {
    test('no status loaded yet -- null does not show chips', () => {
        expect(shouldShowNetworkStatusChips(null)).toBe(false);
    });

    test('both jobs still in flight -- pending does not show chips', () => {
        // The exact case from the review finding: the first poll already
        // landed (mikrotikStatus is non-null) but mergeNetworkStatus says
        // the pair is not done yet.
        expect(shouldShowNetworkStatusChips({ pending: true, secret_status: null, active_session: null })).toBe(false);
    });

    test('one job landed, the other still pending -- still does not show chips', () => {
        expect(shouldShowNetworkStatusChips({ pending: true, secret_status: 'enabled', active_session: null })).toBe(false);
    });

    test('both jobs landed -- shows chips', () => {
        expect(shouldShowNetworkStatusChips({ pending: false, secret_status: 'enabled', active_session: null })).toBe(true);
    });

    test('the outer fetch itself failed -- pending:false still shows the (error) chips', () => {
        // fetchNetworkStatus's catch sets pending:false with secret_error
        // set, so the error can actually reach the user rather than being
        // stuck behind "Checking…" forever.
        expect(shouldShowNetworkStatusChips({
            pending: false,
            secret_status: null,
            secret_error: 'Status check failed',
            active_session: null,
            session_error: null,
        })).toBe(true);
    });

    test('all 15 poll attempts stayed pending -- the post-loop timeout shape shows the (error) chips too', () => {
        // Reviewed bug: fetchNetworkStatus's polling loop used to fall
        // straight through to `finally` when every attempt came back still
        // pending, leaving `pending: true` (and mikrotikStatusLoading false)
        // in state forever -- a permanent, spinner-less "Checking…" with no
        // error and no sign it gave up. The fix lands on this same
        // terminal-with-error shape once the loop is exhausted.
        expect(shouldShowNetworkStatusChips({
            pending: false,
            secret_status: null,
            secret_error: 'Status check timed out.',
            active_session: null,
            session_error: 'Status check timed out.',
        })).toBe(true);
    });
});
