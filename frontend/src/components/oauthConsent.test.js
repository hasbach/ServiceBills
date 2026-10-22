import { consentParams, describeRole } from './oauthConsent';

test('keeps only the OAuth authorize parameters', () => {
    const p = consentParams('?client_id=c1&redirect_uri=https%3A%2F%2Fclaude.ai%2Fcb&response_type=code'
        + '&code_challenge=abc&code_challenge_method=S256&state=s&scope=read&resource=x&evil=1');
    expect(p).toEqual({
        client_id: 'c1', redirect_uri: 'https://claude.ai/cb', response_type: 'code',
        code_challenge: 'abc', code_challenge_method: 'S256', state: 's', scope: 'read', resource: 'x',
    });
});

test('omits missing parameters', () => {
    expect(consentParams('?client_id=c1')).toEqual({ client_id: 'c1' });
});

test('describes roles in plain words', () => {
    expect(describeRole('admin')).toBe('admin');
    expect(describeRole('finance,cashier')).toBe('finance, cashier');
    expect(describeRole('')).toBe('staff');
});
