// Client-side mirror of the server's standard-template rules
// (wa_standard_templates.validate_item; data-model.md §5, FR-015b). The
// server re-validates everything; this only gives the tenant instant
// feedback and keeps Submit off while a selected template is invalid.

export const LIMITS = { header: 60, body: 1024, footer: 60, button: 25 };

// Same shape the server recognises as a variable.
const PLACEHOLDER = /\{([a-z_]+)\}/g;
// Any other single-brace token (`{1}`, `{Customer}`) is almost certainly a
// broken variable, so it's flagged too (the server would send it literally).
const BRACE_TOKEN = /\{([^{}\n]*)\}/g;
const TEMPLATE_NAME = /^[a-z0-9_]{1,512}$/;

export const placeholder = (key) => `{${key}}`;

export function placeholdersIn(text) {
    return Array.from(String(text || '').matchAll(PLACEHOLDER), m => m[1]);
}

function oddTokens(text) {
    const s = String(text || '');
    const out = [];
    if (/\{\{|\}\}/.test(s)) out.push('{{n}}');
    const scrubbed = s.replace(/\{\{[^{}]*\}\}/g, '');
    for (const m of scrubbed.matchAll(BRACE_TOKEN)) {
        if (!/^[a-z_]+$/.test(m[1])) out.push(m[0]);
    }
    return out;
}

export function countPlaceholders(text) {
    const counts = {};
    placeholdersIn(text).forEach(k => { counts[k] = (counts[k] || 0) + 1; });
    return counts;
}

/**
 * Validate one dialog item.
 * @param item { name, header, body, footer, buttons, use_existing, existing,
 *               existingName, variables:[{key,label}], fixed_order }
 * @returns { errors: string[], counts: {key:n} }
 */
export function validateItem(item) {
    const errors = [];
    if (item.use_existing && item.existing) {
        // Nothing goes to Meta; the existing template is linked as-is.
        return { errors, counts: {} };
    }
    const required = (item.variables || []).map(v => v.key);
    const body = item.body || '';
    const found = placeholdersIn(body);
    const counts = countPlaceholders(body);

    const name = (item.name || '').trim();
    if (!name) errors.push('Template name is required');
    else if (!TEMPLATE_NAME.test(name)) errors.push('Template name may only use lowercase letters, numbers and _');
    else if (item.existing && item.existingName && name === item.existingName) {
        errors.push(`A template named "${name}" already exists. Choose "Use existing" or give it a new name`);
    }

    if (!body.trim()) errors.push('Body text is required');
    required.forEach(k => {
        if (!counts[k]) errors.push(`Missing variable ${placeholder(k)}`);
    });
    Object.keys(counts).forEach(k => {
        if (!required.includes(k)) {
            errors.push(required.length
                ? `Unknown variable ${placeholder(k)}: this template can only use ${required.map(placeholder).join(', ')}`
                : `Unknown variable ${placeholder(k)}: this template has no variables`);
        } else if (counts[k] > 1) {
            errors.push(`Variable ${placeholder(k)} must appear exactly once (found ${counts[k]})`);
        }
    });
    const odd = oddTokens(body);
    if (odd.includes('{{n}}')) errors.push('Use "Insert variable" instead of {{1}}-style numbers');
    odd.filter(t => t !== '{{n}}').forEach(t => errors.push(`Unknown variable ${t}`));

    const trimmed = body.trim();
    if (/^\{[a-z_]+\}/.test(trimmed)) errors.push('Body must not start with a variable. Add some words before it');
    if (/\{[a-z_]+\}$/.test(trimmed)) errors.push('Body must not end with a variable. Add some words or punctuation after it');
    if (item.fixed_order && required.length && found.length === required.length
        && required.every(k => counts[k] === 1) && found.some((k, i) => k !== required[i])) {
        errors.push(`The variables of this template must stay in this order: ${required.map(placeholder).join(', ')}`);
    }

    if (body.length > LIMITS.body) errors.push(`Body is longer than ${LIMITS.body} characters`);
    [['Header', item.header, LIMITS.header], ['Footer', item.footer, LIMITS.footer]].forEach(([label, text, max]) => {
        if (!text) return;
        if (text.length > max) errors.push(`${label} is longer than ${max} characters`);
        if (placeholdersIn(text).length || oddTokens(text).length) errors.push(`${label} cannot contain a variable`);
    });
    (item.buttons || []).forEach((b, i) => {
        const t = b?.text || '';
        if (!t.trim()) errors.push(`Button ${i + 1} text is required`);
        else if (t.length > LIMITS.button) errors.push(`Button ${i + 1} text is longer than ${LIMITS.button} characters`);
        if (placeholdersIn(t).length) errors.push(`Button ${i + 1} text cannot contain a variable`);
    });

    return { errors, counts };
}

// Insert `{key}` into `text` at [start, end), returning the new text and the
// caret position right after the inserted placeholder.
export function insertAt(text, start, end, key) {
    const src = String(text || '');
    const s = Math.max(0, Math.min(start ?? src.length, src.length));
    const e = Math.max(s, Math.min(end ?? s, src.length));
    const token = placeholder(key);
    return { text: src.slice(0, s) + token + src.slice(e), caret: s + token.length };
}
