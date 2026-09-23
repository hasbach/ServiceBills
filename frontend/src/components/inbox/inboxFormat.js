import { parseUtc } from '../formatStamp';

export const REASON_META = {
    ai_failed: { label: "AI couldn't answer", color: 'error' },
    escalated: { label: 'Escalated', color: 'error' },
    awaiting_admin: { label: 'Awaiting admin', color: 'warning' },
    unknown_sender: { label: 'Unknown sender', color: 'info' },
    ai_inactive: { label: 'AI off', color: 'default' },
    media_received: { label: 'Media received', color: 'info' },
    send_failed: { label: 'Send failed', color: 'error' },
};

export function describeWindow(expiresAtStamp, nowMs = Date.now()) {
    const expires = parseUtc(expiresAtStamp);
    if (Number.isNaN(expires)) return { open: false, label: 'No customer message yet — templates only' };
    const left = expires - nowMs;
    if (left <= 0) return { open: false, label: 'Window closed — send a template to reopen' };
    const totalMin = Math.floor(left / 60000);
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    return { open: true, label: `Window closes in ${h > 0 ? `${h}h ${m}m` : `${m}m`}` };
}

/** Drop reaction rows and attach them to the message they target. The latest
 *  reaction per side ('in' customer / 'out' us) wins; an empty emoji removes it. */
export function attachReactions(messages) {
    const bySide = {};
    messages.filter(m => m.msg_type === 'reaction').forEach(r => {
        const key = r.reaction_target_wa_id;
        if (!key) return;
        bySide[key] = bySide[key] || {};
        bySide[key][r.direction] = r.reaction_emoji || '';
    });
    return messages.filter(m => m.msg_type !== 'reaction').map(m => {
        const sides = (m.wa_message_id && bySide[m.wa_message_id]) || {};
        const reactions = Object.entries(sides).filter(([, e]) => e).map(([side, emoji]) => ({ emoji, side }));
        return { ...m, reactions };
    });
}

export function templateParamCount(template) {
    const body = (template?.components || []).find(c => (c.type || '').toUpperCase() === 'BODY');
    if (!body?.text) return 0;
    return new Set(body.text.match(/\{\{\d+\}\}/g) || []).size;
}
