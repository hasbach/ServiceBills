import { reasonText, expiryWarnings, updateNotice } from './licenseStatus';

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

describe('updateNotice', () => {
    const at = '2026-09-30T03:30:00Z';
    const ok = { current: '1.2.4', previous: '1.2.3', last_update: { status: 'ok', target: '1.2.4', at, message: '' } };

    test('ok update to current version is a success notice', () => {
        expect(updateNotice(ok, null)).toEqual({
            severity: 'success',
            text: 'ServiceBills was updated to v1.2.4.',
            key: `ok:1.2.4:${at}`,
        });
    });

    test('ok update whose target is not current is ignored', () => {
        expect(updateNotice({ ...ok, current: '1.2.5' }, null)).toBeNull();
    });

    test('failed update includes the message', () => {
        const u = { current: '1.2.3', previous: null, last_update: { status: 'failed', target: '1.2.4', at, message: 'Health check timed out.' } };
        expect(updateNotice(u, null)).toEqual({
            severity: 'error',
            text: 'Update to v1.2.4 failed — still running v1.2.3. Health check timed out.',
            key: `failed:1.2.4:${at}`,
        });
    });

    test('failed update without message omits it', () => {
        const u = { current: '1.2.3', last_update: { status: 'failed', target: '1.2.4', at, message: '' } };
        expect(updateNotice(u, null).text).toBe('Update to v1.2.4 failed — still running v1.2.3.');
    });

    test('skipped, null and missing produce no notice', () => {
        expect(updateNotice({ current: '1.2.3', last_update: { status: 'skipped', target: '1.2.4', at } }, null)).toBeNull();
        expect(updateNotice({ current: '1.2.3', last_update: null }, null)).toBeNull();
        expect(updateNotice(null, null)).toBeNull();
        expect(updateNotice(undefined, null)).toBeNull();
    });

    test('dismissed key hides the notice', () => {
        expect(updateNotice(ok, `ok:1.2.4:${at}`)).toBeNull();
        expect(updateNotice(ok, 'ok:1.2.3:old')).not.toBeNull();
    });
});
