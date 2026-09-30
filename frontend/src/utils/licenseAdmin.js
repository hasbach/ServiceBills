import { moduleLabel } from './licenseStatus.js';

export const TERMS = ['monthly', 'yearly', 'lifetime'];
export const PAID_MODULE_KEYS = ['whatsapp', 'ai_cs', 'network', 'upstream_sync', 'whish_payments'];

const day = (iso) => (iso ? String(iso).slice(0, 10) : null);

export const formatExpiryDay = (term, iso) => (term === 'lifetime' ? 'never' : day(iso) || '—');

// "Network · yearly · 2027-09-30, WhatsApp · lifetime · never"
export const formatModuleSummary = (modules) => {
    const entries = Object.entries(modules || {});
    if (!entries.length) return '—';
    return entries
        .map(([k, m]) => `${moduleLabel(k)} · ${m.term} · ${formatExpiryDay(m.term, m.expires_at)}`)
        .join(', ');
};

// Display-only preview of today + term; server is authoritative.
export const previewExpiry = (term, today = new Date()) => {
    if (term === 'lifetime') return 'never';
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
    if (term === 'yearly') d.setUTCFullYear(d.getUTCFullYear() + 1);
    else d.setUTCMonth(d.getUTCMonth() + 1);
    return d.toISOString().slice(0, 10);
};
