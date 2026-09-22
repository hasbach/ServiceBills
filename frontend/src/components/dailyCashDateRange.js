/**
 * The Daily Cash report reconciles one calendar day in the viewer's own
 * local time, not UTC -- see
 * docs/superpowers/specs/2026-09-22-daily-cash-report-design.md. Given a
 * Date anywhere within a calendar day, returns that day's local-midnight
 * start instant and the following day's local-midnight instant, as ISO
 * strings ready for the API's start_date/end_date params. The backend does
 * no timezone math of its own -- this is where "today" actually gets
 * decided, mirroring how formatStamp.js already trusts the browser's local
 * zone instead of a hardcoded offset.
 */
export function localDayRange(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
  const end = new Date(start);
  end.setDate(end.getDate() + 1);
  return { startIso: start.toISOString(), endIso: end.toISOString() };
}
