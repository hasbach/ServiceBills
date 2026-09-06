/**
 * The API emits every timestamp as UTC, formatted
 * `datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')` — no zone marker at all.
 * Printed verbatim, that reads hours behind for anyone not on UTC: measured
 * 2026-09-06, the agent's "last seen" showed 11:31 to a user whose clock said
 * 14:31. Appending 'Z' before parsing is the one missing step, and it is
 * exactly what describeAge already does for relative labels.
 */
export function parseUtc(stamp) {
    if (!stamp) return NaN;
    return Date.parse(String(stamp).replace(' ', 'T') + 'Z');
}

/** A UTC API stamp rendered in the viewer's own timezone. */
export function formatStamp(stamp) {
    const ms = parseUtc(stamp);
    if (Number.isNaN(ms)) return '—';
    return new Date(ms).toLocaleString();
}
