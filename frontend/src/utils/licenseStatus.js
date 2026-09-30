const REASONS = {
    no_license: 'Your 30-day trial has ended or no license is installed.',
    invalid_signature: 'The installed license file is not valid.',
    machine_mismatch: 'This license belongs to a different computer.',
    expired: 'Your ServiceBills license has expired.',
    clock_rollback: "This computer's date looks wrong. Correct the date and time.",
    revoked: 'This license has been revoked.',
    version_not_covered: 'This version was released after your license expired. Renew your license.',
};

export const MODULE_LABELS = {
    base: 'ServiceBills',
    whatsapp: 'WhatsApp',
    ai_cs: 'AI customer service',
    ai_customer_service: 'AI customer service',
    network: 'Network',
    upstream_sync: 'Upstream sync',
    whish: 'Whish payments',
    whish_payments: 'Whish payments',
};

export const moduleLabel = (scope) => MODULE_LABELS[scope] || scope;

export const reasonText = (reason) =>
    REASONS[reason] || 'There is a problem with your ServiceBills license.';

const DAY_MS = 86400000;

export const expiryWarnings = (license, today = new Date()) => {
    const list = (license && license.warnings) || [];
    return list.map(w => ({
        label: moduleLabel(w.scope),
        daysLeft: Math.max(0, Math.ceil((new Date(w.expires_at) - today) / DAY_MS)),
    }));
};
