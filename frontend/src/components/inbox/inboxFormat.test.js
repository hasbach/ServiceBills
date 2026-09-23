import { describeWindow, attachReactions, REASON_META, templateParamCount } from './inboxFormat';

const NOW = Date.parse('2026-09-23T12:00:00Z');

test('describeWindow open with hours and minutes', () => {
    expect(describeWindow('2026-09-23 17:12:00', NOW)).toEqual({ open: true, label: 'Window closes in 5h 12m' });
});

test('describeWindow under an hour', () => {
    expect(describeWindow('2026-09-23 12:30:00', NOW)).toEqual({ open: true, label: 'Window closes in 30m' });
});

test('describeWindow closed or missing', () => {
    expect(describeWindow('2026-09-23 11:59:00', NOW).open).toBe(false);
    expect(describeWindow(null, NOW)).toEqual({ open: false, label: 'No customer message yet — templates only' });
});

test('attachReactions folds reactions onto targets, latest per side wins, empty removes', () => {
    const msgs = [
        { id: 1, direction: 'out', msg_type: 'text', wa_message_id: 'w1' },
        { id: 2, direction: 'in', msg_type: 'reaction', reaction_target_wa_id: 'w1', reaction_emoji: '👍' },
        { id: 3, direction: 'in', msg_type: 'reaction', reaction_target_wa_id: 'w1', reaction_emoji: '❤️' },
        { id: 4, direction: 'in', msg_type: 'text', wa_message_id: 'w4' },
        { id: 5, direction: 'out', msg_type: 'reaction', reaction_target_wa_id: 'w4', reaction_emoji: '😂' },
        { id: 6, direction: 'out', msg_type: 'reaction', reaction_target_wa_id: 'w4', reaction_emoji: '' },
    ];
    const out = attachReactions(msgs);
    expect(out.map(m => m.id)).toEqual([1, 4]);
    expect(out[0].reactions).toEqual([{ emoji: '❤️', side: 'in' }]);
    expect(out[1].reactions).toEqual([]);
});

test('REASON_META covers every backend reason', () => {
    ['ai_failed', 'escalated', 'awaiting_admin', 'unknown_sender', 'ai_inactive', 'media_received', 'send_failed']
        .forEach(r => expect(REASON_META[r].label).toBeTruthy());
});

test('templateParamCount counts distinct BODY placeholders', () => {
    expect(templateParamCount({ components: [{ type: 'BODY', text: 'Hi {{1}}, your balance is {{2}} ({{1}})' }] })).toBe(2);
    expect(templateParamCount({ components: [{ type: 'HEADER', text: 'x' }] })).toBe(0);
    expect(templateParamCount({})).toBe(0);
});
