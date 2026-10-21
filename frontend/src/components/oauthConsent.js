// Pure helpers for the MCP connector consent page (OAuthConsentView).
export const AUTHORIZE_KEYS = ['client_id', 'redirect_uri', 'response_type', 'code_challenge',
    'code_challenge_method', 'state', 'scope', 'resource'];

export function consentParams(search) {
    const q = new URLSearchParams(search);
    const out = {};
    AUTHORIZE_KEYS.forEach(k => { if (q.get(k) !== null) out[k] = q.get(k); });
    return out;
}

export function describeRole(role) {
    const parts = (role || '').split(',').map(s => s.trim()).filter(Boolean);
    return parts.length ? parts.join(', ') : 'staff';
}
