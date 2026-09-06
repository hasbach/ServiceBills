import { formatStamp, parseUtc } from './formatStamp';

test('parses the API stamp as UTC, not as local time', () => {
    // The backend emits datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S').
    expect(parseUtc('2026-09-06 11:31:30')).toBe(Date.UTC(2026, 8, 6, 11, 31, 30));
});

test('renders a UTC stamp in the viewer local zone', () => {
    // Whatever zone the test runs in, the rendered value must be the local
    // rendering of that UTC instant -- this is the bug being fixed: the UI
    // used to print the UTC string verbatim, reading hours behind.
    const expected = new Date(Date.UTC(2026, 8, 6, 11, 31, 30)).toLocaleString();
    expect(formatStamp('2026-09-06 11:31:30')).toBe(expected);
});

test('an empty or missing stamp renders as an em dash, never "Invalid Date"', () => {
    expect(formatStamp(null)).toBe('—');
    expect(formatStamp('')).toBe('—');
    expect(formatStamp(undefined)).toBe('—');
});

test('an unparseable stamp renders as an em dash', () => {
    expect(formatStamp('not a date')).toBe('—');
    expect(Number.isNaN(parseUtc('not a date'))).toBe(true);
});
