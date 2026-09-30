import { reasonText, expiryWarnings } from './licenseStatus';

const REASONS = ['no_license', 'invalid_signature', 'machine_mismatch', 'expired', 'clock_rollback', 'revoked', 'version_not_covered'];

test('every reason maps to non-empty text', () => {
    REASONS.forEach(r => expect(reasonText(r).length).toBeGreaterThan(5));
    expect(reasonText('expired')).toBe('Your ServiceBills license has expired.');
});

test('unknown reason gives generic text', () => {
    expect(reasonText('weird')).toMatch(/license/i);
    expect(reasonText(undefined)).toMatch(/license/i);
});

test('expiryWarnings computes daysLeft and labels', () => {
    const lic = { warnings: [
        { scope: 'base', expires_at: '2026-10-10T00:00:00Z' },
        { scope: 'network', expires_at: '2026-10-03T12:00:00Z' },
        { scope: 'whatsapp', expires_at: '2026-10-01T00:00:00Z' },
    ] };
    const got = expiryWarnings(lic, new Date('2026-09-30T00:00:00Z'));
    expect(got).toEqual([
        { label: 'ServiceBills', daysLeft: 10 },
        { label: 'Network', daysLeft: 4 },
        { label: 'WhatsApp', daysLeft: 1 },
    ]);
});

test('expiryWarnings handles missing license', () => {
    expect(expiryWarnings(null)).toEqual([]);
    expect(expiryWarnings({})).toEqual([]);
});
