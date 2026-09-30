import { formatModuleSummary, previewExpiry, formatExpiryDay } from './licenseAdmin.js';

test('formatModuleSummary', () => {
    expect(formatModuleSummary({})).toBe('—');
    expect(formatModuleSummary({
        network: { term: 'yearly', expires_at: '2027-09-30T00:00:00Z' },
        whatsapp: { term: 'lifetime', expires_at: null },
    })).toBe('Network · yearly · 2027-09-30, WhatsApp · lifetime · never');
});

test('previewExpiry', () => {
    const t = new Date('2026-09-30T10:00:00Z');
    expect(previewExpiry('yearly', t)).toBe('2027-09-30');
    expect(previewExpiry('monthly', t)).toBe('2026-10-30');
    expect(previewExpiry('lifetime', t)).toBe('never');
    expect(formatExpiryDay('monthly', null)).toBe('—');
});
