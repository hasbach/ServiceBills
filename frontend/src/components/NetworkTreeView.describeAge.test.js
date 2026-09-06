// Plain-function tests for describeAge -- the one place a UTC-vs-local slip
// would silently read the wrong number of hours (see NetworkTreeView.js: the
// API emits '%Y-%m-%d %H:%M:%S' with no timezone marker, and describeAge
// appends 'Z' after swapping the space for 'T' to parse it as UTC rather than
// whatever offset the test runner's machine happens to be in).
//
// NetworkTreeView.js pulls in '../context/AppContext', which pulls in the
// real `axios` package -- whose installed build ships an ESM-only
// `index.js` that this project's jest config (plain CRA, no
// transformIgnorePatterns override) cannot parse. Mocking AppContext here
// keeps this a true plain-function test of describeAge without dragging
// that unrelated resolution problem in; App.test.js already shows
// @testing-library/react itself isn't installed, so a real render of this
// component isn't exercised anywhere in this project today.
jest.mock('../context/AppContext', () => ({
    apiService: {},
    useAppContext: () => ({ setSnackbar: () => {}, user: null }),
}));

import { describeAge, isStale } from './NetworkTreeView';

describe('describeAge', () => {
    it('reports "never checked" for a null/empty stamp', () => {
        expect(describeAge(null)).toBe('never checked');
        expect(describeAge('')).toBe('never checked');
        expect(describeAge(undefined)).toBe('never checked');
    });

    it('parses the stamp as UTC, not local time', () => {
        // If this were parsed as local time on a machine west of UTC (e.g.
        // America/New_York, UTC-4/-5), 'now' minus the stamp would be
        // negative-going-on-hours-larger than it really is, and this would
        // read as "X h ago" instead of "just now". Explicitly picking a
        // `now` a few seconds after the same instant, expressed in UTC,
        // pins this down regardless of the host machine's own timezone.
        const stamp = '2026-09-05 12:00:00';
        const now = Date.UTC(2026, 8, 5, 12, 0, 30); // 30s later, same instant
        expect(describeAge(stamp, now)).toBe('just now');
    });

    it('rounds down to whole minutes', () => {
        const stamp = '2026-09-05 12:00:00';
        const now = Date.UTC(2026, 8, 5, 12, 4, 59);
        expect(describeAge(stamp, now)).toBe('4 min ago');
    });

    it('switches to hours at 60 minutes', () => {
        const stamp = '2026-09-05 12:00:00';
        const now = Date.UTC(2026, 8, 5, 13, 0, 0);
        expect(describeAge(stamp, now)).toBe('1 h ago');
    });

    it('switches to days at 24 hours', () => {
        const stamp = '2026-09-05 12:00:00';
        const now = Date.UTC(2026, 8, 7, 12, 0, 0);
        expect(describeAge(stamp, now)).toBe('2 d ago');
    });

    it('returns an empty string for an unparseable stamp rather than throwing', () => {
        expect(describeAge('not-a-date')).toBe('');
    });
});

// isStale decides whether the auto-refresh effect fires a fresh (in 'direct'
// mode, potentially blocking -- see STALE_AFTER_MS's comment in
// NetworkTreeView.js) check against the production worker. It had no direct
// tests before this: describeAge's suite above exercised the same UTC-parse
// expression, but never the staleness cutoff itself.
describe('isStale', () => {
    it('treats a null/empty stamp as stale', () => {
        expect(isStale(null)).toBe(true);
        expect(isStale('')).toBe(true);
        expect(isStale(undefined)).toBe(true);
    });

    it('treats an unparseable stamp as stale', () => {
        expect(isStale('not-a-date')).toBe(true);
    });

    it('is not stale for a stamp just inside the 30-minute window', () => {
        const stamp = '2026-09-05 12:00:00';
        // 30 min minus 1s later, same instant expressed in UTC.
        const now = Date.UTC(2026, 8, 5, 12, 29, 59);
        expect(isStale(stamp, now)).toBe(false);
    });

    it('is stale for a stamp just outside the 30-minute window', () => {
        const stamp = '2026-09-05 12:00:00';
        // 30 min plus 1s later.
        const now = Date.UTC(2026, 8, 5, 12, 30, 1);
        expect(isStale(stamp, now)).toBe(true);
    });
});
